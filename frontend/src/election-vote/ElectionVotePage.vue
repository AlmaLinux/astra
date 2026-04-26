<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import type {
  ElectionVoteBootstrap,
  ElectionVoteCandidate,
  ElectionVotePayload,
  VoteBreakdownRow,
  VoteSubmitError,
  VoteSubmitSuccess,
} from "./types";

const props = defineProps<{
  bootstrap: ElectionVoteBootstrap;
}>();

const isLoading = ref(false);
const loadError = ref("");
const election = ref<ElectionVotePayload["election"] | null>(null);
const candidates = ref<ElectionVoteCandidate[]>([]);
const voteWeightBreakdown = ref<VoteBreakdownRow[]>([]);
const credentialPublicId = ref("");
const ranking = ref<ElectionVoteCandidate[]>([]);
const rankingUsernameFallback = ref("");
const rankingError = ref("");
const resultMessage = ref("");
const resultIsError = ref(false);
const isSubmitting = ref(false);
const ballotHash = ref("");
const nonce = ref("");
const previousChainHash = ref("");
const chainHash = ref("");
const showVoteBreakdown = ref(false);
const receiptInput = ref<HTMLInputElement | null>(null);

const availableCandidates = computed(() =>
  candidates.value.filter((candidate) => !ranking.value.some((rankedCandidate) => rankedCandidate.id === candidate.id)),
);
const hasReceipt = computed(() => ballotHash.value !== "");
const hasRankingInput = computed(() => ranking.value.length > 0 || rankingUsernameFallback.value.trim() !== "");
const submitLabel = computed(() => {
  if (isSubmitting.value) {
    return "Submitting...";
  }
  if (hasReceipt.value) {
    return "Submit replacement ballot";
  }
  return "Submit vote";
});
const verifyReceiptHref = computed(() => {
  if (ballotHash.value === "") {
    return props.bootstrap.verifyUrl;
  }
  return `${props.bootstrap.verifyUrl}?receipt=${encodeURIComponent(ballotHash.value)}`;
});

function formatWindow(rawDateTime: string): string {
  return `${rawDateTime.replace("T", " ").replace(/([+-]\d\d:\d\d|Z)$/, "").slice(0, 16)} UTC`;
}

function readCsrfToken(): string {
  const parts = document.cookie.split(";");
  for (const part of parts) {
    const trimmed = part.trim();
    if (trimmed.startsWith("csrftoken=")) {
      return decodeURIComponent(trimmed.slice("csrftoken=".length));
    }
  }
  return "";
}

function prefillCredentialFromHash(): void {
  const hash = window.location.hash.trim();
  if (hash === "" || hash === "#") {
    return;
  }

  const params = new URLSearchParams(hash.slice(1));
  const credential = params.get("credential")?.trim() ?? "";
  if (credential === "") {
    return;
  }

  credentialPublicId.value = credential;

  try {
    window.history.replaceState(null, document.title, window.location.pathname + window.location.search);
  } catch {
    // Leave the fragment intact if history replacement is unavailable.
  }
}

function setResult(message: string, isError: boolean): void {
  resultMessage.value = message;
  resultIsError.value = isError;
}

function clearRankingError(): void {
  rankingError.value = "";
}

function validateRanking(): boolean {
  if (hasRankingInput.value) {
    clearRankingError();
    return true;
  }
  rankingError.value = 'Add a candidate to your ranking from the "Candidates" box.';
  return false;
}

function clearReceipt(): void {
  ballotHash.value = "";
  nonce.value = "";
  previousChainHash.value = "";
  chainHash.value = "";
}

function setReceipt(payload: VoteSubmitSuccess): void {
  ballotHash.value = payload.ballot_hash;
  nonce.value = payload.nonce;
  previousChainHash.value = payload.previous_chain_hash;
  chainHash.value = payload.chain_hash;
}

function addCandidate(candidate: ElectionVoteCandidate): void {
  if (ranking.value.some((rankedCandidate) => rankedCandidate.id === candidate.id)) {
    return;
  }
  ranking.value = [...ranking.value, candidate];
  clearRankingError();
}

function moveCandidate(index: number, direction: -1 | 1): void {
  const nextIndex = index + direction;
  if (nextIndex < 0 || nextIndex >= ranking.value.length) {
    return;
  }

  const nextRanking = [...ranking.value];
  const [candidate] = nextRanking.splice(index, 1);
  nextRanking.splice(nextIndex, 0, candidate);
  ranking.value = nextRanking;
  clearRankingError();
}

function removeCandidate(candidateId: number): void {
  ranking.value = ranking.value.filter((candidate) => candidate.id !== candidateId);
  clearRankingError();
}

function copyReceiptWithSelectionFallback(): boolean {
  const input = receiptInput.value;
  if (!input) {
    return false;
  }
  input.focus();
  input.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  }
}

async function copyReceipt(): Promise<void> {
  if (ballotHash.value === "") {
    return;
  }

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(ballotHash.value);
      setResult("Receipt copied to clipboard.", false);
      return;
    }
  } catch {
    // Fall through to the selection-based fallback below.
  }

  if (copyReceiptWithSelectionFallback()) {
    setResult("Receipt copied to clipboard.", false);
  } else {
    setResult("Copy failed. Please copy the receipt manually.", true);
  }
}

async function load(): Promise<void> {
  isLoading.value = true;
  loadError.value = "";

  try {
    const response = await fetch(props.bootstrap.apiUrl, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });

    if (!response.ok) {
      loadError.value = "Unable to load the ballot right now.";
      return;
    }

    const payload = (await response.json()) as ElectionVotePayload;
    election.value = payload.election;
    candidates.value = payload.candidates;
    voteWeightBreakdown.value = payload.vote_weight_breakdown;
  } catch {
    loadError.value = "Unable to load the ballot right now.";
  } finally {
    isLoading.value = false;
  }
}

async function submitVote(): Promise<void> {
  if (!validateRanking()) {
    clearReceipt();
    return;
  }

  if (election.value === null || credentialPublicId.value.trim() === "") {
    setResult("Voting credential is required.", true);
    clearReceipt();
    return;
  }

  isSubmitting.value = true;
  setResult("", false);

  try {
    const response = await fetch(election.value.submit_url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": readCsrfToken(),
      },
      body: JSON.stringify({
        credential_public_id: credentialPublicId.value.trim(),
        ranking: ranking.value.map((candidate) => candidate.id),
        ranking_usernames: rankingUsernameFallback.value.trim(),
      }),
    });

    const payload = (await response.json()) as VoteSubmitSuccess | VoteSubmitError;
    if (!response.ok || payload.ok !== true) {
      setResult(payload.ok === false ? payload.error : "Vote submission failed.", true);
      clearReceipt();
      return;
    }

    setReceipt(payload);
    if (payload.email_queued) {
      setResult("Your vote was recorded. A receipt was sent to your email.", false);
    } else {
      setResult("Your vote was recorded. (A receipt email could not be sent.)", false);
    }
  } catch {
    setResult("Vote submission failed.", true);
    clearReceipt();
  } finally {
    isSubmitting.value = false;
  }
}

onMounted(async () => {
  prefillCredentialFromHash();
  await load();
});
</script>

<template>
  <div data-election-vote-vue-root>
    <div v-if="election !== null" class="row">
      <div class="col-lg-7">
        <div class="card card-primary">
          <div class="card-header">
            <h3 class="card-title">Ballot</h3>
          </div>
          <div class="card-body">
            <div v-if="isLoading" class="text-muted">Loading ballot...</div>
            <div v-else-if="loadError" class="alert alert-danger mb-3">{{ loadError }}</div>
            <template v-else>
              <div class="text-muted small mb-3">
                Voting window: {{ formatWindow(election.start_datetime) }} → {{ formatWindow(election.end_datetime) }}
                <br />
                Note: Election administrators may extend the end date if quorum is not reached.
              </div>

              <form id="election-vote-form" method="post" :action="election.submit_url" :data-verify-url="props.bootstrap.verifyUrl" @keydown.enter="validateRanking" @submit.prevent="submitVote">
              <div class="form-group">
                <label for="election-credential">Voting credential</label>
                <input
                  id="election-credential"
                  v-model="credentialPublicId"
                  class="form-control"
                  type="text"
                  name="credential_public_id"
                  autocomplete="off"
                  required
                />
                <small class="form-text text-muted">Paste the credential you received. Keep it private.</small>
                <small class="form-text text-muted mb-3">
                  Submitting again replaces your previous ballot. Only your latest ballot submitted before voting closes is counted.
                </small>

                <template v-if="election.voter_votes !== null">
                  <p v-if="election.voter_votes > 0" class="form-text mb-0">
                    You have <strong>{{ election.voter_votes }}</strong> vote{{ election.voter_votes === 1 ? "" : "s" }} for this election.
                    <span v-if="election.voter_votes > 1 && voteWeightBreakdown.length > 0" class="position-relative d-inline-block">
                      <button
                        id="vote-breakdown-tooltip"
                        type="button"
                        class="btn btn-link p-0 align-baseline border-0"
                        style="font-size: inherit; vertical-align: baseline;"
                        data-placement="right"
                        aria-label="How your vote count is computed"
                        aria-describedby="vote-breakdown-tooltip-content"
                        :aria-expanded="showVoteBreakdown"
                        @click="showVoteBreakdown = !showVoteBreakdown"
                        @mouseenter="showVoteBreakdown = true"
                        @mouseleave="showVoteBreakdown = false"
                        @focus="showVoteBreakdown = true"
                        @blur="showVoteBreakdown = false"
                      >
                        <i class="fas fa-info-circle text-muted" aria-hidden="true"></i>
                      </button>
                      <div
                        id="vote-breakdown-tooltip-content"
                        class="tooltip vote-breakdown-tooltip bs-tooltip-right"
                        :class="{ show: showVoteBreakdown }"
                        role="tooltip"
                        :style="{ position: 'absolute', left: '1.25rem', top: '-0.65rem', zIndex: 1070, minWidth: '18rem', pointerEvents: 'none' }"
                      >
                        <div class="arrow"></div>
                        <div class="tooltip-inner text-left">
                          <div class="vote-breakdown-lines">
                            <div v-for="(row, index) in voteWeightBreakdown" :key="`${row.label}-${row.org_name ?? 'none'}-${row.votes}`" class="vote-breakdown-line">
                              <span class="vote-breakdown-votes">{{ index === 0 ? "" : "+ " }}{{ row.votes }}</span>
                              <span class="vote-breakdown-sep">:</span>
                              <span class="vote-breakdown-text">{{ row.org_name ? "Representative of " : "" }}{{ row.label }} member{{ row.org_name ? ` (${row.org_name})` : "" }}</span>
                            </div>

                            <div class="border-top my-1"></div>

                            <div class="vote-breakdown-total">
                              <span class="vote-breakdown-votes">{{ election.voter_votes }}</span>
                              <span class="vote-breakdown-sep">:</span>
                              <span class="vote-breakdown-text">Total Vote{{ election.voter_votes === 1 ? "" : "s" }}</span>
                            </div>
                          </div>
                        </div>
                      </div>
                    </span>
                  </p>
                  <small v-if="election.voter_votes > 1" class="text-muted">
                    These votes are applied as extra weight to your single ranked ballot.
                  </small>
                  <p v-else class="form-text">You do not appear to be eligible for this election.</p>
                </template>
              </div>

              <div class="form-group">
                <label>Ranking</label>
                <div id="election-ranking-list" class="mb-2">
                  <div
                    v-for="(candidate, index) in ranking"
                    :key="candidate.id"
                    class="d-flex align-items-center justify-content-between border rounded px-2 py-1 mb-2"
                  >
                    <div class="d-flex align-items-center text-truncate pr-2">
                      <span class="badge badge-secondary mr-2">{{ index + 1 }}</span>
                      <span class="text-truncate">{{ candidate.label }}</span>
                    </div>
                    <div>
                      <button type="button" class="btn btn-sm btn-outline-secondary mr-1" @click="moveCandidate(index, -1)">↑</button>
                      <button type="button" class="btn btn-sm btn-outline-secondary mr-1" @click="moveCandidate(index, 1)">↓</button>
                      <button type="button" class="btn btn-sm btn-outline-danger" @click="removeCandidate(candidate.id)">Remove</button>
                    </div>
                  </div>
                </div>
                <input id="election-ranking-input" type="hidden" name="ranking" :value="ranking.map((candidate) => candidate.id).join(',')" />
                <div id="election-ranking-error" class="text-danger small" :class="{ 'd-none': !rankingError }" role="alert">{{ rankingError }}</div>
                <div id="election-ranking-hint" class="text-muted small" :class="{ 'd-none': hasRankingInput || Boolean(rankingError) }">Add a candidate to your ranking from the "Candidates" box.</div>
                <div id="election-ranking-order" class="text-muted small" :class="{ 'd-none': !hasRankingInput }">Use the buttons on the right to order your ranking.</div>
              </div>

              <div id="election-vote-result" :class="['alert', resultMessage === '' ? 'd-none' : '', resultIsError ? 'alert-danger' : 'alert-success']" role="alert">
                {{ resultMessage }}
              </div>

              <div id="election-receipt-box" :class="hasReceipt ? '' : 'd-none'">
                <label class="text-muted" for="election-receipt">Ballot receipt code</label>
                <div class="input-group">
                  <input id="election-receipt" ref="receiptInput" class="form-control" :value="ballotHash" type="text" readonly />
                  <div class="input-group-append">
                    <button id="election-receipt-copy" type="button" class="btn btn-outline-secondary" title="Copy receipt to clipboard" @click="copyReceipt">Copy</button>
                  </div>
                </div>
                <div class="mt-2">
                  <a id="election-receipt-verify" :href="verifyReceiptHref" target="_blank" rel="noopener noreferrer">Verify this receipt</a>
                </div>
                <div class="mt-2">
                  <label class="text-muted" for="election-nonce">Submission Nonce</label>
                  <input id="election-nonce" class="form-control" :value="nonce" type="text" readonly />
                  <div class="form-text text-muted">Save this together with your receipt.</div>
                </div>
                <div class="mt-2">
                  <label class="text-muted" for="election-previous-chain-hash">Previous chain hash</label>
                  <input
                    id="election-previous-chain-hash"
                    class="form-control"
                    :value="previousChainHash"
                    type="text"
                    readonly
                  />
                </div>
                <div class="mt-2">
                  <label class="text-muted" for="election-chain-hash">Chain hash</label>
                  <input id="election-chain-hash" class="form-control" :value="chainHash" type="text" readonly />
                </div>
              </div>

              <button
                id="election-submit-button"
                type="submit"
                class="btn btn-primary btn-block mt-2"
                title="Submit your ballot"
                :aria-disabled="isSubmitting || !election.can_submit_vote"
                :disabled="isSubmitting || !election.can_submit_vote"
                @click="validateRanking"
              >
                {{ submitLabel }}
              </button>

              <div class="mt-3">
                <details>
                  <summary class="text-muted">No-JS fallback</summary>
                  <p class="text-muted mb-2">If you can't use the ranking UI, enter usernames as comma-separated values (first choice first).</p>
                  <input v-model="rankingUsernameFallback" class="form-control" type="text" name="ranking_usernames" placeholder="e.g. alice,bob,charlie" @input="clearRankingError" />
                </details>
              </div>
              </form>
            </template>
          </div>
        </div>
      </div>

      <div class="col-lg-5">
        <div class="card card-primary">
          <div class="card-header">
            <h3 class="card-title">Candidates</h3>
          </div>
          <div class="card-body">
            <div v-if="isLoading" class="text-muted">Loading candidates...</div>
            <template v-else>
              <div v-for="candidate in candidates" :key="candidate.id" class="d-flex align-items-center justify-content-between mb-2">
                <div class="text-truncate pr-2">
                  <div class="font-weight-bold">{{ candidate.label }}</div>
                </div>
                <button
                  type="button"
                  class="btn btn-sm btn-outline-primary"
                  data-action="election-add"
                  :data-candidate-id="candidate.id"
                  :data-candidate-label="candidate.label"
                  title="Add this candidate to your ranking"
                  :disabled="!availableCandidates.some((availableCandidate) => availableCandidate.id === candidate.id)"
                  @click="addCandidate(candidate)"
                >
                  Add to ranking
                </button>
              </div>
            </template>
          </div>
        </div>
      </div>

      <div class="row">
        <div class="col-12 col-md-6">
          <div class="card card-outline card-info">
            <div class="card-header">
              <h3 class="card-title">How ranking works</h3>
            </div>
            <div class="card-body">
              <p class="mb-2">Rank candidates in order of preference.</p>
              <ul>
                <li>Your 1st choice counts first.</li>
                <li>If your top choice is elected with enough support, or is eliminated, your vote can transfer to your next ranked choice.</li>
                <li>If you stop ranking, your vote will no longer transfer.</li>
                <li>All rankings are treated equally — ranking additional candidates does not disadvantage your higher preferences.</li>
              </ul>
              <p class="mt-2 mb-0">Votes are weighted according to membership or sponsorship level. You submit one ranked ballot, and all rankings follow the same rules.</p>
            </div>
          </div>
        </div>

        <div class="col-12 col-md-6">
          <div class="card card-outline card-info">
            <div class="card-header">
              <h3 class="card-title">How winners are chosen</h3>
            </div>
            <div class="card-body">
              <p class="mb-2">
                This election uses a ranked-choice system called
                <strong><a href="https://en.wikipedia.org/wiki/Single_transferable_vote">Single Transferable Vote (STV)</a></strong>,
                designed to select multiple winners fairly.
              </p>
              <ul class="mb-3">
                <li>Candidates are elected once they have enough total support.</li>
                <li>If a candidate has more support than needed, the extra portion of votes is shared with voters' next choices.</li>
                <li>If a candidate is eliminated, their votes are transferred to the next ranked candidates.</li>
                <li>This process continues until all open seats are filled.</li>
              </ul>
              <p class="mb-0">
                Votes are counted using <strong>Meek STV (High-Precision Variant)</strong>.
                This is a Meek-family STV method with fractional transfers, implemented using fixed-point arithmetic at <strong>80-digit precision</strong> to eliminate rounding ambiguity.
                The count is <strong>fully deterministic</strong> (the same ballots always produce the same result) and produces a detailed audit trail that can be <strong>independently verified</strong>. For details, see the election audit log and published artifacts.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>