<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";

import {
  buildBallotVerifyUrl,
  readBallotVerifyReceipt,
  type BallotVerifyBootstrap,
  type BallotVerifyResponse,
} from "./types";
import { fillUrlTemplate } from "../shared/urlTemplates";

const props = defineProps<{
  bootstrap: BallotVerifyBootstrap;
}>();

const receipt = ref("");
const result = ref<BallotVerifyResponse | null>(null);
const isLoading = ref(false);
const error = ref("");

function syncUrl(pushState: boolean): void {
  const nextUrl = buildBallotVerifyUrl(window.location.pathname, receipt.value);
  if (pushState) {
    window.history.pushState(null, "", nextUrl);
    return;
  }
  window.history.replaceState(null, "", nextUrl);
}

async function load(pushState: boolean): Promise<void> {
  isLoading.value = true;
  error.value = "";

  try {
    const query = receipt.value.trim() !== "" ? `?receipt=${encodeURIComponent(receipt.value.trim())}` : "";
    const response = await fetch(`${props.bootstrap.apiUrl}${query}`, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    const payload = (await response.json()) as BallotVerifyResponse;
    result.value = payload;
    syncUrl(pushState);
    if (!response.ok && !payload.rate_limited) {
      error.value = "Unable to verify this ballot right now.";
    }
  } catch {
    error.value = "Unable to verify this ballot right now.";
  } finally {
    isLoading.value = false;
  }
}

async function onSubmit(): Promise<void> {
  await load(true);
}

function onPopState(): void {
  receipt.value = readBallotVerifyReceipt(window.location.href);
  void load(false);
}

function electionDetailHref(electionId: number): string {
  return fillUrlTemplate(props.bootstrap.electionDetailUrlTemplate, "__election_id__", electionId);
}

function auditLogHref(electionId: number): string {
  return fillUrlTemplate(props.bootstrap.auditLogUrlTemplate, "__election_id__", electionId);
}

onMounted(async () => {
  receipt.value = readBallotVerifyReceipt(window.location.href);
  window.addEventListener("popstate", onPopState);

  if (receipt.value !== "") {
    await load(false);
  }
});

onBeforeUnmount(() => {
  window.removeEventListener("popstate", onPopState);
});
</script>

<template>
  <div data-ballot-verify-vue-root>
    <div class="row justify-content-center">
      <div class="col-md-10 col-lg-8">
        <div class="card card-primary">
          <div class="card-header">
            <h3 class="card-title">Ballot receipt verification</h3>
          </div>

          <div class="card-body">
            <div v-if="result?.rate_limited" class="alert alert-danger" role="alert">
              <strong>Too many verification requests.</strong> Please wait a moment and try again.
            </div>

            <p class="text-muted">
              Enter the 64-character receipt you received after submitting your ballot. This page confirms whether a ballot with that receipt is recorded. It does not show your selections, your identity, or exact timestamps.
            </p>

            <p class="text-muted">
              After the election closes, we will publish an anonymized ballots ledger and a public audit log so the final result can be independently verified. The winner calculation uses <strong>Meek STV (High-Precision Variant)</strong>, implemented with <strong>80-digit precision</strong> to eliminate rounding ambiguity and ensure deterministic recounts.
            </p>

            <div class="alert alert-info" role="alert">
              <strong>Local verification scripts</strong>
              <ul>
                <li>Recompute your ballot hash locally: <a :href="bootstrap.verifyBallotHashUrl" download>Download verify-ballot-hash.py</a></li>
                <li>Verify the public ballot chain after the election closes: <a :href="bootstrap.verifyBallotChainUrl" download>Download verify-ballot-chain.py</a></li>
                <li>Verify Rekor audit-log attestations after the election closes: <a :href="bootstrap.verifyAuditLogUrl" download>Download verify-audit-log.py</a></li>
              </ul>
            </div>

            <form method="get" action="/elections/ballot/verify/" class="mb-4" novalidate @submit.prevent="onSubmit">
              <div class="form-group">
                <label for="id_receipt">Ballot receipt code</label>
                <input
                  id="id_receipt"
                  v-model="receipt"
                  type="text"
                  class="form-control"
                  name="receipt"
                  placeholder="64-character lowercase hex"
                  autocomplete="off"
                  spellcheck="false"
                />
              </div>

              <button type="submit" class="btn btn-primary" title="Check receipt in the ballot ledger">Verify</button>
            </form>

            <div v-if="isLoading" class="text-muted">Verifying ballot receipt...</div>
            <div v-else-if="error !== ''" class="alert alert-danger">{{ error }}</div>
            <template v-else-if="result?.has_query">
              <div v-if="!result.is_valid_receipt" class="alert alert-danger" role="alert">
                <strong>Invalid receipt.</strong> Please enter a 64-character lowercase hex value.
              </div>
              <div v-else-if="!result.found" class="alert alert-warning" role="alert">
                <strong>No ballot with this receipt was found.</strong>
              </div>
              <div v-else class="card">
                <div class="card-body">
                  <h5>Ballot status</h5>
                  <div class="alert alert-success mb-3" role="alert">
                    Yes — a ballot with this receipt is recorded for this election.
                  </div>

                  <h5>Tally status</h5>
                  <p v-if="result.election_status === 'open'">
                    This election is still open, so the final tally is not available yet. We will not report whether any ballot is included in the final tally until after the election closes.
                  </p>
                  <template v-else-if="result.election_status === 'closed'">
                    <p v-if="result.is_superseded">
                      This ballot was replaced by a later submission from the same voter and will not be included in the upcoming tally.
                    </p>
                    <p v-else>This ballot is recorded and locked and will be included in the upcoming tally.</p>
                  </template>
                  <template v-else-if="result.election_status === 'tallied'">
                    <p v-if="result.is_superseded">
                      This ballot was replaced by a later submission from the same voter and was not included in the final tally.
                    </p>
                    <p v-else>This ballot was included in the final tally.</p>
                  </template>

                  <template v-if="result.public_ballots_url || result.election">
                    <h5>Public verification</h5>
                    <ul>
                      <li v-if="result.public_ballots_url"><a :href="result.public_ballots_url">Public ballots ledger (JSON)</a></li>
                      <li v-if="result.election"><a :href="auditLogHref(result.election.id)">Audit log</a></li>
                    </ul>
                  </template>

                  <h5>Ballot Information</h5>
                  <ul>
                    <li><strong>Election:</strong> <a v-if="result.election" :href="electionDetailHref(result.election.id)">{{ result.election.name }}</a></li>
                    <li><strong>Election status:</strong> {{ result.election_status }}</li>
                    <li><strong>Submission date:</strong> {{ result.submitted_date }}</li>
                    <li>
                      <strong>Ballot status:</strong>
                      {{ result.is_superseded ? 'Superseded (replaced by a later submission)' : 'Final ballot submission' }}
                    </li>
                  </ul>

                  <template v-if="result.verification_snippet">
                    <h5>Copy/paste constants for local verification</h5>
                    <p class="text-muted">
                      Copy/paste the block below into <strong><a :href="bootstrap.verifyBallotHashUrl" download>verify-ballot-hash.py</a></strong>. It includes the election ID, candidate username-to-ID mapping, and where to find your voting credential. You still need to enter your own vote choices in the script (vote choices are secret and are not shown on this page).
                    </p>
                    <pre class="bg-light p-3"><code>{{ result.verification_snippet }}</code></pre>
                  </template>
                </div>
              </div>
            </template>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>