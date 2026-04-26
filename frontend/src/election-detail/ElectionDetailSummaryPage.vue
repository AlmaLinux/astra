<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";

import {
  buildElectionDetailRouteUrl,
  readElectionDetailRouteState,
  type ElectionCandidateItem,
  type ElectionCandidatesResponse,
  type ElectionDetailBootstrap,
  type ElectionDetailRouteState,
  type ElectionInfoPayload,
  type ElectionInfoResponse,
} from "./types";

const props = defineProps<{
  bootstrap: ElectionDetailBootstrap;
}>();

const election = ref<ElectionInfoPayload | null>(null);
const candidateRows = ref<ElectionCandidateItem[]>([]);
const currentCandidatePage = ref(1);
const isLoading = ref(false);
const candidatesLoading = ref(false);
const error = ref("");
const candidatesError = ref("");
const turnoutChartCanvas = ref<HTMLCanvasElement | null>(null);
let turnoutChart: { destroy?: () => void } | null = null;
let popStateHandler: (() => void) | null = null;

type ChartConstructor = new (...args: unknown[]) => { destroy?: () => void };

function currentRouteState(): ElectionDetailRouteState {
  return {
    pathname: window.location.pathname,
    candidatePage: currentCandidatePage.value,
  };
}

function applyRouteState(routeState: ElectionDetailRouteState): void {
  currentCandidatePage.value = routeState.candidatePage;
}

function syncUrl(pushState: boolean): void {
  const nextUrl = buildElectionDetailRouteUrl(currentRouteState(), window.location.href);
  if (pushState) {
    window.history.pushState(null, "", nextUrl);
    return;
  }
  window.history.replaceState(null, "", nextUrl);
}

function statusLabel(status: string): string {
  if (status === "open") {
    return "Open";
  }
  if (status === "closed") {
    return "Closed";
  }
  if (status === "tallied") {
    return "Tallied";
  }
  return status;
}

function descriptionLines(value: string): string[] {
  return value.split(/\r?\n/);
}

function profileUrl(username: string): string {
  return props.bootstrap.userProfileUrlTemplate.replace("__username__", encodeURIComponent(username));
}

function destroyTurnoutChart(): void {
  turnoutChart?.destroy?.();
  turnoutChart = null;
}

async function renderTurnoutChart(): Promise<void> {
  destroyTurnoutChart();
  const payload = election.value?.turnout_chart_data;
  if (!election.value?.show_turnout_chart || !payload || payload.labels.length === 0) {
    return;
  }

  await nextTick();

  const chartFactory = (window as Window & typeof globalThis & { Chart?: ChartConstructor }).Chart;
  const canvas = turnoutChartCanvas.value;
  if (!chartFactory || !canvas) {
    return;
  }

  const context = canvas.getContext("2d");
  if (!context) {
    return;
  }

  turnoutChart = new chartFactory(context, {
    type: "bar",
    data: {
      labels: payload.labels,
      datasets: [
        {
          label: "Ballots submitted",
          backgroundColor: "rgba(60,141,188,0.9)",
          borderColor: "rgba(60,141,188,0.8)",
          data: payload.counts,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          beginAtZero: true,
          ticks: {
            precision: 0,
          },
        },
      },
      plugins: {
        legend: {
          display: false,
        },
      },
    },
  });
}

async function load(pushState: boolean): Promise<void> {
  isLoading.value = true;
  error.value = "";

  try {
    const [infoOk, candidatesOk] = await Promise.all([loadInfo(), loadCandidates(false)]);
    if (!infoOk || !candidatesOk) {
      error.value = "Unable to load election details right now.";
      return;
    }
    syncUrl(pushState);
    await renderTurnoutChart();
  } catch {
    error.value = "Unable to load election details right now.";
  } finally {
    isLoading.value = false;
  }
}

async function loadInfo(): Promise<boolean> {
  try {
    const response = await fetch(props.bootstrap.infoApiUrl, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!response.ok) {
      return false;
    }
    const payload = (await response.json()) as ElectionInfoResponse;
    election.value = payload.election;
    return true;
  } catch {
    return false;
  }
}

async function loadCandidates(pushState: boolean): Promise<boolean> {
  candidatesLoading.value = true;
  candidatesError.value = "";
  try {
    const params = new URLSearchParams();
    if (currentCandidatePage.value > 1) {
      params.set("page", String(currentCandidatePage.value));
    }
    const query = params.toString() ? `?${params.toString()}` : "";
    const response = await fetch(`${props.bootstrap.candidatesApiUrl}${query}`, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!response.ok) {
      candidatesError.value = "Unable to load candidates right now.";
      return false;
    }

    const payload = (await response.json()) as ElectionCandidatesResponse;
    candidateRows.value = payload.candidates.items;
    currentCandidatePage.value = payload.candidates.pagination.page;
    if (pushState) {
      syncUrl(true);
    }
    return true;
  } catch {
    candidatesError.value = "Unable to load candidates right now.";
    return false;
  } finally {
    candidatesLoading.value = false;
  }
}

onMounted(async () => {
  applyRouteState(readElectionDetailRouteState(window.location.href));
  popStateHandler = () => {
    applyRouteState(readElectionDetailRouteState(window.location.href));
    void load(false);
  };
  window.addEventListener("popstate", popStateHandler);
  await load(false);
});

onBeforeUnmount(() => {
  if (popStateHandler !== null) {
    window.removeEventListener("popstate", popStateHandler);
    popStateHandler = null;
  }
  destroyTurnoutChart();
});
</script>

<template>
  <div data-election-detail-vue-root>
    <div v-if="error" class="alert alert-danger">{{ error }}</div>

    <template v-else-if="election">
      <div class="card card-outline card-primary">
        <div class="card-body">
          <dl class="row mb-0">
            <dt class="col-sm-4">Status</dt>
            <dd class="col-sm-8">{{ statusLabel(election.status) }}</dd>
            <dt class="col-sm-4">Voting window</dt>
            <dd class="col-sm-8">
              {{ election.start_datetime_display }} → {{ election.end_datetime_display }}
              <i
                v-if="election.status === 'open'"
                class="fas fa-info-circle text-muted"
                data-toggle="tooltip"
                title="Election administrators may extend the end date if quorum is not reached."
              ></i>
            </dd>
            <dt class="col-sm-4">Seats</dt>
            <dd class="col-sm-8">{{ election.number_of_seats }}</dd>
          </dl>

          <p v-if="election.description">{{ election.description }}</p>

          <p v-if="election.url" class="mb-2">
            <strong>URL:</strong>
            <a :href="election.url" target="_blank" rel="noopener noreferrer">{{ election.url }}</a>
          </p>
        </div>
      </div>

      <div v-if="election.status === 'tallied'" class="row">
        <div class="col-12">
          <div class="card card-success">
            <div class="card-header">
              <h3 class="card-title">Results</h3>
            </div>
            <div class="card-body">
              <div v-if="election.tally_winners.length > 0">
                <h5 class="mb-2"><strong>Elected</strong></h5>
                <ul>
                  <li v-for="winner in election.tally_winners" :key="winner.username">
                    {{ winner.full_name }}
                    (<a :href="profileUrl(winner.username)">{{ winner.username }}</a>)
                  </li>
                </ul>
                <p v-if="election.empty_seats > 0"><strong>Empty seats:</strong> {{ election.empty_seats }}</p>
              </div>

              <div v-if="Object.keys(election.turnout_stats || {}).length > 0">
                <h5 class="mb-2"><strong>Participation</strong></h5>
                <div class="mb-3 text-muted">
                  Quorum:
                  <template v-if="election.turnout_stats.quorum_required">
                    {{ election.turnout_stats.required_participating_voter_count }} voters and
                    {{ election.turnout_stats.required_participating_vote_weight_total }} vote weight
                    ({{ election.turnout_stats.quorum_percent }}%)
                    <span v-if="election.turnout_stats.quorum_met" class="ml-1 badge badge-success">met</span>
                    <span v-else class="ml-1 badge badge-danger">not met</span>
                  </template>
                  <template v-else>not required</template>
                </div>

                <div class="mb-2">
                  <strong>Number of unique voters</strong>
                  &mdash;
                  {{ election.turnout_stats.participating_voter_count }} / {{ election.turnout_stats.eligible_voter_count }} ({{ election.turnout_stats.participating_voter_percent }}%)
                </div>

                <div class="progress mb-2">
                  <div
                    class="progress-bar bg-success"
                    role="progressbar"
                    :aria-valuenow="election.turnout_stats.participating_voter_count"
                    aria-valuemin="0"
                    :aria-valuemax="election.turnout_stats.eligible_voter_count"
                    :style="{ width: `${election.turnout_stats.participating_voter_percent}%` }"
                  >
                    <span class="sr-only">{{ election.turnout_stats.participating_voter_percent }}% Complete</span>
                  </div>
                </div>

                <div class="mb-2">
                  <strong>Votes cast</strong>
                  &mdash;
                  {{ election.turnout_stats.participating_vote_weight_total }} / {{ election.turnout_stats.eligible_vote_weight_total }} ({{ election.turnout_stats.participating_vote_weight_percent }}%)
                </div>
                <div class="progress mb-0">
                  <div
                    class="progress-bar bg-info"
                    role="progressbar"
                    :aria-valuenow="election.turnout_stats.participating_vote_weight_total"
                    aria-valuemin="0"
                    :aria-valuemax="election.turnout_stats.eligible_vote_weight_total"
                    :style="{ width: `${election.turnout_stats.participating_vote_weight_percent}%` }"
                  >
                    <span class="sr-only">{{ election.turnout_stats.participating_vote_weight_percent }}% Complete</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div v-if="election.show_turnout_chart && election.turnout_chart_data.labels.length > 0" class="row">
        <div class="col-12">
          <div class="card card-outline card-secondary">
            <div class="card-header">
              <h3 class="card-title">Participation{{ election.status === "open" ? " so far" : "" }}</h3>
            </div>
            <div class="card-body">
              <div class="row">
                <div v-if="election.status === 'open'" class="col-12 col-lg-4">
                  <div class="mb-3 text-muted">
                    Quorum:
                    <template v-if="election.turnout_stats.quorum_required">
                      {{ election.turnout_stats.required_participating_voter_count }} voters and
                      {{ election.turnout_stats.required_participating_vote_weight_total }} vote weight
                      ({{ election.turnout_stats.quorum_percent }}%)
                      <span v-if="election.turnout_stats.quorum_met" class="ml-1 badge badge-success">met</span>
                      <span v-else class="ml-1 badge badge-danger">not met</span>
                    </template>
                    <template v-else>not required</template>
                  </div>

                  <div class="mb-2">
                    <strong>Number of unique voters</strong>
                    &mdash;
                    {{ election.turnout_stats.participating_voter_count }} / {{ election.turnout_stats.eligible_voter_count }} ({{ election.turnout_stats.participating_voter_percent }}%)
                  </div>
                  <div class="progress mb-2">
                    <div class="progress-bar bg-success" role="progressbar" :aria-valuenow="election.turnout_stats.participating_voter_count" aria-valuemin="0" :aria-valuemax="election.turnout_stats.eligible_voter_count" :style="{ width: `${election.turnout_stats.participating_voter_percent}%` }">
                      <span class="sr-only">{{ election.turnout_stats.participating_voter_percent }}% Complete</span>
                    </div>
                  </div>

                  <div class="mb-2">
                    <strong>Votes cast</strong>
                    &mdash;
                    {{ election.turnout_stats.participating_vote_weight_total }} / {{ election.turnout_stats.eligible_vote_weight_total }} ({{ election.turnout_stats.participating_vote_weight_percent }}%)
                  </div>
                  <div class="progress mb-0">
                    <div class="progress-bar bg-info" role="progressbar" :aria-valuenow="election.turnout_stats.participating_vote_weight_total" aria-valuemin="0" :aria-valuemax="election.turnout_stats.eligible_vote_weight_total" :style="{ width: `${election.turnout_stats.participating_vote_weight_percent}%` }">
                      <span class="sr-only">{{ election.turnout_stats.participating_vote_weight_percent }}% Complete</span>
                    </div>
                  </div>
                </div>

                <div class="col-12" :class="{ 'col-lg-8 mt-3 mt-lg-0': election.status === 'open' }">
                  <p class="mb-2"><strong>Ballots submitted over time (including superseded ballots)</strong></p>
                  <div class="chart">
                    <canvas ref="turnoutChartCanvas" style="min-height: 250px; height: 250px; max-height: 250px; max-width: 100%;"></canvas>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="row">
        <div class="col-12">
          <div class="card card-outline card-success">
            <div class="card-header">
              <h3 class="card-title">Candidates</h3>
            </div>
            <div class="card-body">
              <div v-if="candidatesError" class="text-muted">{{ candidatesError }}</div>
              <div v-else-if="candidatesLoading" class="text-muted">Loading candidates...</div>
              <div v-else class="row">
                <div v-for="candidate in candidateRows" :key="candidate.id" class="col-12 col-md-6 col-xl-6 mb-3">
                  <div class="card card-primary h-100">
                    <div class="card-header">
                      <h3 class="card-title mb-0">
                        <template v-if="candidate.has_user">{{ candidate.full_name }}</template>
                        (<a :href="profileUrl(candidate.username)">{{ candidate.username }}</a>)
                      </h3>
                    </div>
                    <div class="card-body">
                      <div v-if="candidate.avatar_url" style="float:left; margin-right:1rem; margin-bottom:0.5rem;">
                        <img
                          :src="candidate.avatar_url"
                          class="img-circle"
                          style="width:120px;height:120px;object-fit:cover;"
                          alt="Avatar"
                          :title="candidate.full_name"
                        >
                      </div>
                      <p v-if="candidate.description" class="mb-2">
                        <template v-for="(line, index) in descriptionLines(candidate.description)" :key="`${candidate.id}-${index}`">
                          <br v-if="index > 0">
                          {{ line }}
                        </template>
                      </p>

                      <p v-if="candidate.url" class="mb-2">
                        <a :href="candidate.url" target="_blank" rel="noopener noreferrer">{{ candidate.url }}</a>
                      </p>

                      <hr class="candidate-card-divider" />
                      <p class="mb-0">
                        <strong>Nominated by</strong>
                        —
                        {{ candidate.nominator_display_name }}
                        <template v-if="candidate.nominator_profile_username">
                          (<a :href="profileUrl(candidate.nominator_profile_username)">{{ candidate.nominator_profile_username }}</a>)
                        </template>
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              <div v-if="election.exclusion_group_messages.length > 0" class="card card-outline card-danger mt-3">
                <div class="card-body">
                  <p v-for="message in election.exclusion_group_messages" :key="message" class="mb-2">{{ message }}</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.candidate-card-divider {
  clear: both;
}
</style>