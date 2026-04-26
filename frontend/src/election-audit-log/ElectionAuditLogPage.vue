<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";

import {
  buildElectionAuditRouteUrl,
  readElectionAuditRouteState,
  type ElectionAuditBallotEntry,
  type ElectionAuditBootstrap,
  type ElectionAuditItem,
  type ElectionAuditLogResponse,
  type ElectionAuditRouteState,
  type ElectionAuditSummaryResponse,
} from "./types";
import { buildSankeyChart, type SankeyFlow } from "../shared/sankeyChart";
import { fillUrlTemplate } from "../shared/urlTemplates";

const props = defineProps<{
  bootstrap: ElectionAuditBootstrap;
}>();

const items = ref<ElectionAuditItem[]>([]);
const pagination = ref<ElectionAuditLogResponse["audit_log"]["pagination"] | null>(null);
const jumpLinks = ref<ElectionAuditLogResponse["audit_log"]["jump_links"]>([]);
const summary = ref<ElectionAuditSummaryResponse["summary"] | null>(null);
const currentPage = ref(1);
const auditLoading = ref(false);
const error = ref("");
const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
let sankeyChart: { destroy?: () => void } | null = null;
let sankeyRenderTimeoutId: number | null = null;

type ChartConstructor = new (...args: unknown[]) => { destroy?: () => void };
type WindowWithChart = Window & typeof globalThis & { Chart?: ChartConstructor };
type GlobalWithChart = typeof globalThis & { Chart?: ChartConstructor };

const groupedItems = computed(() => {
  const groups: Array<{ dateLabel: string; items: ElectionAuditItem[] }> = [];
  for (const item of items.value) {
    const dateLabel = formatDateLabel(item.timestamp);
    const lastGroup = groups.at(-1);
    if (lastGroup && lastGroup.dateLabel === dateLabel) {
      lastGroup.items.push(item);
      continue;
    }
    groups.push({ dateLabel, items: [item] });
  }
  return groups;
});

function currentRouteState(): ElectionAuditRouteState {
  return {
    pathname: window.location.pathname,
    page: currentPage.value,
  };
}

function applyRouteState(routeState: ElectionAuditRouteState): void {
  currentPage.value = routeState.page;
}

function syncUrl(pushState: boolean): void {
  const nextBaseUrl = buildElectionAuditRouteUrl(currentRouteState());
  const nextUrl = pushState ? nextBaseUrl : `${nextBaseUrl}${window.location.hash}`;
  if (pushState) {
    window.history.pushState(null, "", nextUrl);
    return;
  }
  window.history.replaceState(null, "", nextUrl);
}

function profileUrl(username: string): string {
  return fillUrlTemplate(props.bootstrap.userProfileUrlTemplate, "__username__", username);
}

function pageUrl(pageNumber: number | null): string {
  return buildElectionAuditRouteUrl({ pathname: window.location.pathname, page: pageNumber || 1 });
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "";
  }
  return value.replace("T", " ").replace(/([+-]\d\d:\d\d|Z)$/, "").slice(0, 19);
}

function formatTime(value: string | null | undefined): string {
  return formatDateTime(value).slice(11, 19);
}

function formatDateLabel(value: string | null | undefined): string {
  if (!value) {
    return "Unknown";
  }
  const year = value.slice(0, 4);
  const monthIndex = Number.parseInt(value.slice(5, 7), 10) - 1;
  const day = Number.parseInt(value.slice(8, 10), 10);
  return `${day} ${monthNames[monthIndex] || value.slice(5, 7)} ${year}`;
}

function formatStaticDateTime(value: string): string {
  const compact = value.replace("T", " ").replace(/([+-]\d\d:\d\d|Z)$/, "").slice(0, 16);
  const timezoneLabel = value.endsWith("Z") || value.endsWith("+00:00") ? " UTC" : "";
  return `${compact}${timezoneLabel}`;
}

function formatNumber4(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") {
    return "";
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(4) : String(value);
}

function pluralize(count: number | undefined, singular: string, plural: string): string {
  return count === 1 ? singular : plural;
}

function payloadValue(item: ElectionAuditItem, key: string): string {
  const value = item.payload[key];
  if (value === null || value === undefined || value === "") {
    return "";
  }
  return String(value);
}

function payloadBooleanText(item: ElectionAuditItem, key: string): string {
  return item.payload[key] ? "true" : "false";
}

function payloadCandidates(item: ElectionAuditItem): Array<{ id: unknown; freeipa_username: unknown; tiebreak_uuid: unknown }> {
  const candidates = item.payload.candidates;
  return Array.isArray(candidates) ? candidates : [];
}

function textLines(value: string | null | undefined): string[] {
  const text = String(value || "");
  if (!text) {
    return [];
  }
  return text.split(/\r?\n/);
}

function shortHash(value: string | null | undefined): string {
  if (!value) {
    return "";
  }
  if (value.length <= 16) {
    return value;
  }
  return `${value.slice(0, 16)}…`;
}

function ballotEntryKey(entry: ElectionAuditBallotEntry): string {
  return `${entry.timestamp || ""}-${entry.ballot_hash}`;
}

function copyText(text: string): void {
  const trimmed = text.trim();
  if (!trimmed) {
    return;
  }
  if (navigator.clipboard?.writeText) {
    void navigator.clipboard.writeText(trimmed);
    return;
  }
  window.prompt("Copy:", trimmed);
}

function destroySankeyChart(): void {
  sankeyChart?.destroy?.();
  sankeyChart = null;
}

function cancelQueuedSankeyChartRender(): void {
  if (sankeyRenderTimeoutId === null) {
    return;
  }
  window.clearTimeout(sankeyRenderTimeoutId);
  sankeyRenderTimeoutId = null;
}

function queueSankeyChartRender(): void {
  cancelQueuedSankeyChartRender();
  sankeyRenderTimeoutId = window.setTimeout(() => {
    sankeyRenderTimeoutId = null;
    void renderSankeyChart();
  }, 0);
}

async function renderSankeyChart(): Promise<void> {
  destroySankeyChart();

  const flows = summary.value?.sankey_flows || [];
  if (flows.length === 0) {
    return;
  }

  await nextTick();

  const canvas = document.getElementById("tally-sankey-chart") as HTMLCanvasElement | null;
  const chartFactory = (window as WindowWithChart).Chart ?? (globalThis as GlobalWithChart).Chart;
  if (!canvas || !canvas.getContext || !chartFactory) {
    return;
  }

  sankeyChart = buildSankeyChart(
    chartFactory,
    canvas,
    flows as SankeyFlow[],
    summary.value?.sankey_elected_nodes || [],
    summary.value?.sankey_eliminated_nodes || [],
  );
}

async function loadSummary(): Promise<boolean> {
  try {
    const response = await fetch(props.bootstrap.summaryApiUrl, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!response.ok) {
      return false;
    }
    const payload = (await response.json()) as ElectionAuditSummaryResponse;
    summary.value = payload.summary;
    return true;
  } catch {
    return false;
  }
}

async function loadAuditLog(pushState: boolean): Promise<boolean> {
  try {
    const query = currentPage.value > 1 ? `?page=${currentPage.value}` : "";
    const response = await fetch(`${props.bootstrap.apiUrl}${query}`, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!response.ok) {
      return false;
    }
    const payload = (await response.json()) as ElectionAuditLogResponse;
    items.value = payload.audit_log.items;
    pagination.value = payload.audit_log.pagination;
    jumpLinks.value = payload.audit_log.jump_links;
    currentPage.value = payload.audit_log.pagination.page;
    syncUrl(pushState);
    return true;
  } catch {
    return false;
  }
}

async function load(pushState: boolean, includeSummary = false): Promise<void> {
  auditLoading.value = true;
  error.value = "";

  if (includeSummary) {
    void loadSummary().then((summaryOk) => {
      if (summaryOk) {
        queueSankeyChartRender();
      }
    });
  }

  try {
    const auditOk = await loadAuditLog(pushState);
    if (!auditOk) {
      error.value = "Unable to load the election audit log right now.";
      return;
    }
  } catch {
    error.value = "Unable to load the election audit log right now.";
  } finally {
    auditLoading.value = false;
  }

  if (!includeSummary && summary.value) {
    queueSankeyChartRender();
  }
}

function jumpToAnchor(anchor: string): void {
  if (!anchor) {
    return;
  }
  window.history.pushState(null, "", `${buildElectionAuditRouteUrl(currentRouteState())}#${encodeURIComponent(anchor)}`);
  document.getElementById(anchor)?.scrollIntoView({ block: "start", behavior: "smooth" });
}

async function onPageChange(pageNumber: number | null): Promise<void> {
  if (!pageNumber) {
    return;
  }
  currentPage.value = pageNumber;
  await load(true);
}

function onPopState(): void {
  applyRouteState(readElectionAuditRouteState(window.location.href));
  void load(false);
}

onMounted(async () => {
  applyRouteState(readElectionAuditRouteState(window.location.href));
  window.addEventListener("popstate", onPopState);
  await load(false, true);
});

onBeforeUnmount(() => {
  window.removeEventListener("popstate", onPopState);
  cancelQueuedSankeyChartRender();
  destroySankeyChart();
});
</script>

<template>
  <div data-election-audit-log-vue-root>
    <div v-if="error" class="alert alert-danger">{{ error }}</div>
    <div v-else class="row">
      <div class="col-lg-4">
        <div class="card card-outline card-primary">
          <div class="card-header">
            <h3 class="card-title">Election</h3>
          </div>
          <div class="card-body">
            <dl class="row mb-0">
              <dt class="col-sm-5">Name</dt>
              <dd class="col-sm-7">{{ bootstrap.name }}</dd>
              <dt class="col-sm-5">Status</dt>
              <dd class="col-sm-7">{{ bootstrap.status }}</dd>
              <dt class="col-sm-5">Voting window</dt>
              <dd class="col-sm-7">{{ formatStaticDateTime(bootstrap.startDatetime) }} → {{ formatStaticDateTime(bootstrap.endDatetime) }}</dd>
              <dt class="col-sm-5">Seats</dt>
              <dd class="col-sm-7">{{ bootstrap.numberOfSeats }}</dd>
              <dt class="col-sm-5">Unique voters</dt>
              <dd class="col-sm-7">{{ summary?.ballots_cast ?? 0 }}</dd>
              <dt class="col-sm-5">Votes cast</dt>
              <dd class="col-sm-7">{{ summary?.votes_cast ?? 0 }}</dd>
              <dt class="col-sm-5">Counting method</dt>
              <dd class="col-sm-7">{{ bootstrap.algorithmName || "Unknown" }}</dd>
              <dt class="col-sm-5">Algorithm version</dt>
              <dd class="col-sm-7">{{ bootstrap.algorithmVersion || "1.0" }}</dd>
              <dt class="col-sm-5">Specification</dt>
              <dd class="col-sm-7"><a :href="bootstrap.algorithmUrl">Election algorithm</a></dd>
              <dt class="col-sm-5">Quota (Droop)</dt>
              <dd class="col-sm-7">{{ formatNumber4(summary?.quota) }}</dd>
            </dl>

            <hr class="my-3">

            <a class="btn btn-outline-primary btn-block" :href="bootstrap.detailUrl" title="Return to election details">Back to election page</a>
            <a class="btn btn-outline-secondary btn-block" :href="bootstrap.publicBallotsUrl" title="Download ballots (JSON)">Download ballots (JSON)</a>
            <a class="btn btn-outline-secondary btn-block" :href="bootstrap.publicAuditUrl" title="Download audit log (JSON)">Download audit log (JSON)</a>
          </div>
        </div>

        <div v-if="bootstrap.status === 'tallied' && summary" class="card card-outline card-success">
          <div class="card-header">
            <h3 class="card-title">Tally summary</h3>
          </div>
          <div class="card-body">
            <template v-if="summary.tally_elected_users.length > 0">
              <p class="mb-2"><strong>Elected:</strong></p>
              <ul class="pl-3 ml-2">
                <li v-for="winner in summary.tally_elected_users" :key="winner.username">
                  {{ winner.full_name }} (<a :href="profileUrl(winner.username)">{{ winner.username }}</a>)
                </li>
              </ul>
              <p v-if="summary.empty_seats > 0"><strong>Empty seats:</strong> {{ summary.empty_seats }}</p>
            </template>
            <p v-else class="mb-0">No elected list was recorded.</p>
          </div>
        </div>
      </div>

      <div class="col-lg-8">
        <div class="card card-outline card-info">
          <div class="card-header">
            <h3 class="card-title">Timeline</h3>
          </div>
          <div class="card-body">
            <div id="timeline-top" class="d-flex flex-wrap align-items-center justify-content-between mb-2">
              <div class="btn-group btn-group-sm mb-2" role="group" aria-label="Jump to">
                <a v-for="jump in jumpLinks" :key="jump.anchor" class="btn btn-outline-secondary" :href="`#${jump.anchor}`" :title="`Jump to ${jump.label}`" @click.prevent="jumpToAnchor(jump.anchor)">{{ jump.label }}</a>
              </div>

              <div v-if="pagination && (pagination.has_previous || pagination.has_next)" class="btn-group btn-group-sm mb-2" role="group" aria-label="Timeline navigation">
                <a v-if="pagination.has_previous" class="btn btn-outline-secondary" :href="pageUrl(pagination.previous_page_number)" title="Show newer timeline events" @click.prevent="onPageChange(pagination.previous_page_number)">Newer</a>
                <a v-if="pagination.has_next" class="btn btn-outline-secondary" :href="pageUrl(pagination.next_page_number)" title="Load older timeline events" @click.prevent="onPageChange(pagination.next_page_number)">Load older</a>
              </div>
            </div>

            <div v-if="auditLoading" class="text-muted">Loading audit log...</div>
            <div v-else class="timeline">
              <template v-if="groupedItems.length > 0">
                <template v-for="group in groupedItems" :key="group.dateLabel">
                  <div class="time-label">
                    <span class="bg-green">{{ group.dateLabel }}</span>
                  </div>
                  <div v-for="item in group.items" :key="`${item.event_type}-${item.timestamp}-${item.title}`">
                  <i :class="`${item.icon} ${item.icon_bg}`"></i>
                  <div class="timeline-item" :id="item.anchor || undefined">
                    <span class="time"><i class="fas fa-clock"></i> {{ formatTime(item.timestamp) }}</span>
                    <h3 class="timeline-header">{{ item.title }}</h3>
                    <div class="timeline-body">
                      <template v-if="item.event_type === 'tally_round'">
                        <p v-if="item.summary_text" class="mb-2">
                          <template v-for="(line, lineIndex) in textLines(item.summary_text)" :key="`summary-${lineIndex}`">
                            {{ line }}<br v-if="lineIndex < textLines(item.summary_text).length - 1">
                          </template>
                        </p>
                      </template>

                      <template v-else-if="item.event_type === 'ballots_submitted_summary'">
                        <p class="mb-2"><strong>{{ item.ballots_count }}</strong> {{ pluralize(item.ballots_count, "ballot", "ballots") }} submitted.</p>
                        <p v-if="item.first_timestamp && item.last_timestamp" class="text-muted small mb-2">{{ formatTime(item.first_timestamp) }} → {{ formatTime(item.last_timestamp) }}</p>

                        <details class="mb-2">
                          <summary class="text-muted">Show ballot hashes</summary>
                          <ul class="mb-2 pl-3 mt-2">
                            <li v-for="entry in item.ballot_entries || []" :key="ballotEntryKey(entry)">
                              <span class="text-muted">{{ formatTime(entry.timestamp) }}</span>
                              <template v-if="entry.ballot_hash">
                                — <code :title="entry.ballot_hash">{{ shortHash(entry.ballot_hash) }}</code>
                                <span v-if="entry.supersedes_ballot_hash" class="text-muted small">
                                  (supersedes <code :title="entry.supersedes_ballot_hash">{{ shortHash(entry.supersedes_ballot_hash) }}</code>)
                                </span>
                                <button type="button" class="btn btn-outline-secondary btn-xs ml-2 js-copy-hash" :data-hash="entry.ballot_hash" aria-label="Copy full ballot hash" title="Copy full ballot hash" @click="copyText(entry.ballot_hash)">
                                  <i class="fas fa-copy" aria-hidden="true"></i>
                                </button>
                              </template>
                            </li>
                            <li v-if="!item.ballot_entries || item.ballot_entries.length === 0" class="text-muted">No ballot details recorded.</li>
                          </ul>

                          <p v-if="item.ballots_preview_truncated" class="text-muted small mb-2">
                            Showing first {{ item.ballots_preview_limit }} {{ pluralize(item.ballots_preview_limit, "ballot", "ballots") }}.
                          </p>
                        </details>
                      </template>

                      <template v-else-if="item.event_type === 'election_end_extended'">
                        <p class="mb-2"><strong>Election end was extended</strong></p>
                        <dl class="row mb-2">
                          <template v-if="payloadValue(item, 'previous_end_datetime')"><dt class="col-sm-5">Previous end</dt><dd class="col-sm-7">{{ payloadValue(item, "previous_end_datetime") }}</dd></template>
                          <template v-if="payloadValue(item, 'new_end_datetime')"><dt class="col-sm-5">New end</dt><dd class="col-sm-7">{{ payloadValue(item, "new_end_datetime") }}</dd></template>
                          <template v-if="payloadValue(item, 'quorum_percent')"><dt class="col-sm-5">Quorum percent</dt><dd class="col-sm-7">{{ payloadValue(item, "quorum_percent") }}%</dd></template>
                          <template v-if="payloadValue(item, 'required_participating_voter_count')"><dt class="col-sm-5">Quorum voters required</dt><dd class="col-sm-7">{{ payloadValue(item, "required_participating_voter_count") }}</dd></template>
                          <template v-if="payloadValue(item, 'required_participating_vote_weight_total')"><dt class="col-sm-5">Quorum vote weight required</dt><dd class="col-sm-7">{{ payloadValue(item, "required_participating_vote_weight_total") }}</dd></template>
                          <template v-if="payloadValue(item, 'participating_voter_count')"><dt class="col-sm-5">Voters participated</dt><dd class="col-sm-7">{{ payloadValue(item, "participating_voter_count") }}</dd></template>
                          <template v-if="payloadValue(item, 'participating_vote_weight_total')"><dt class="col-sm-5">Vote weight participated</dt><dd class="col-sm-7">{{ payloadValue(item, "participating_vote_weight_total") }}</dd></template>
                        </dl>
                      </template>

                      <template v-else-if="item.event_type === 'quorum_reached'">
                        <p class="mb-2"><strong>Quorum reached</strong></p>
                        <dl class="row mb-2">
                          <template v-if="payloadValue(item, 'quorum_percent')"><dt class="col-sm-5">Quorum percent</dt><dd class="col-sm-7">{{ payloadValue(item, "quorum_percent") }}%</dd></template>
                          <template v-if="payloadValue(item, 'required_participating_voter_count')"><dt class="col-sm-5">Voters required</dt><dd class="col-sm-7">{{ payloadValue(item, "required_participating_voter_count") }}</dd></template>
                          <template v-if="payloadValue(item, 'required_participating_vote_weight_total')"><dt class="col-sm-5">Vote weight required</dt><dd class="col-sm-7">{{ payloadValue(item, "required_participating_vote_weight_total") }}</dd></template>
                          <template v-if="payloadValue(item, 'participating_voter_count')"><dt class="col-sm-5">Voters participated</dt><dd class="col-sm-7">{{ payloadValue(item, "participating_voter_count") }}</dd></template>
                          <template v-if="payloadValue(item, 'participating_vote_weight_total')"><dt class="col-sm-5">Vote weight participated</dt><dd class="col-sm-7">{{ payloadValue(item, "participating_vote_weight_total") }}</dd></template>
                          <template v-if="payloadValue(item, 'eligible_voter_count')"><dt class="col-sm-5">Eligible voters</dt><dd class="col-sm-7">{{ payloadValue(item, "eligible_voter_count") }}</dd></template>
                        </dl>
                      </template>

                      <template v-else-if="item.event_type === 'election_started'">
                        <p class="mb-2">
                          <strong>Genesis chain head:</strong>
                          <code class="ml-2" :title="payloadValue(item, 'genesis_chain_hash')">{{ shortHash(payloadValue(item, "genesis_chain_hash")) }}</code>
                          <button type="button" class="btn btn-outline-secondary btn-xs ml-2 js-copy-hash" :data-hash="payloadValue(item, 'genesis_chain_hash')" aria-label="Copy genesis chain head" title="Copy genesis chain head" @click="copyText(payloadValue(item, 'genesis_chain_hash'))"><i class="fas fa-copy" aria-hidden="true"></i></button>
                        </p>
                        <template v-if="payloadCandidates(item).length > 0">
                          <p class="mb-1"><strong>Candidates (tie-break order):</strong></p>
                          <table class="table table-sm table-borderless mb-2" style="font-size:0.85em;">
                            <thead><tr><th>Username</th><th>ID</th><th>Tie-break UUID</th></tr></thead>
                            <tbody>
                              <tr v-for="candidate in payloadCandidates(item)" :key="String(candidate.id)">
                                <td>{{ candidate.freeipa_username }}</td>
                                <td>{{ candidate.id }}</td>
                                <td><code>{{ candidate.tiebreak_uuid }}</code></td>
                              </tr>
                            </tbody>
                          </table>
                        </template>
                      </template>

                      <template v-else-if="item.event_type === 'election_closed'">
                        <p class="mb-2">
                          <strong>Final chain head:</strong>
                          <code class="ml-2" :title="payloadValue(item, 'chain_head')">{{ shortHash(payloadValue(item, "chain_head")) }}</code>
                          <button type="button" class="btn btn-outline-secondary btn-xs ml-2 js-copy-hash" :data-hash="payloadValue(item, 'chain_head')" aria-label="Copy final chain head" title="Copy final chain head" @click="copyText(payloadValue(item, 'chain_head'))"><i class="fas fa-copy" aria-hidden="true"></i></button>
                        </p>
                        <template v-if="item.payload.credentials_affected !== undefined || item.payload.emails_scrubbed !== undefined">
                          <p class="mb-2"><strong>Credentials anonymized:</strong> {{ payloadBooleanText(item, "credentials_affected") }}</p>
                          <p class="mb-2"><strong>Emails scrubbed:</strong> {{ payloadBooleanText(item, "emails_scrubbed") }}</p>
                        </template>
                      </template>

                      <template v-else-if="item.event_type === 'election_anonymized'">
                        <p class="mb-2">Voter credentials anonymized and sensitive emails scrubbed.</p>
                        <dl class="row mb-0">
                          <dt class="col-sm-6">Credentials anonymized</dt>
                          <dd class="col-sm-6">{{ payloadValue(item, "credentials_affected") || 0 }}</dd>
                          <dt class="col-sm-6">Emails scrubbed</dt>
                          <dd class="col-sm-6">{{ payloadValue(item, "emails_scrubbed") || 0 }}</dd>
                          <template v-if="item.payload.scrub_anomaly === true"><dt class="col-sm-6">Scrub anomaly detected</dt><dd class="col-sm-6">true</dd></template>
                        </dl>
                      </template>

                      <template v-else-if="item.event_type === 'tally_completed'">
                        <template v-if="item.elected_users && item.elected_users.length > 0">
                          <p class="mb-2"><strong>Elected:</strong></p>
                          <ul class="pl-3 ml-2">
                            <li v-for="winner in item.elected_users" :key="winner.username">
                              {{ winner.full_name }} (<a :href="profileUrl(winner.username)">{{ winner.username }}</a>)
                            </li>
                          </ul>
                        </template>
                        <p v-if="summary && summary.empty_seats > 0" class="mt-2"><strong>Empty seats:</strong> {{ summary.empty_seats }}</p>
                        <div v-if="summary && summary.sankey_flows.length > 0" class="mt-3">
                          <div class="chart election-sankey-chart">
                            <canvas
                              id="tally-sankey-chart"
                              data-sankey-chart
                              aria-label="Vote flow by round"
                              role="img"
                            ></canvas>
                          </div>
                        </div>
                      </template>

                      <template v-else>
                        <p v-if="item.summary_text" class="mb-2">
                          <template v-for="(line, lineIndex) in textLines(item.summary_text)" :key="`summary-${lineIndex}`">
                            {{ line }}<br v-if="lineIndex < textLines(item.summary_text).length - 1">
                          </template>
                        </p>
                        <p v-if="item.audit_text" class="mb-2">
                          <template v-for="(line, lineIndex) in textLines(item.audit_text)" :key="`audit-${lineIndex}`">
                            {{ line }}<br v-if="lineIndex < textLines(item.audit_text).length - 1">
                          </template>
                        </p>
                      </template>

                      <div v-if="item.round_rows && item.round_rows.length > 0" class="table-responsive">
                        <table class="table table-sm table-striped mb-2">
                          <thead>
                            <tr>
                              <th>Candidate</th>
                              <th class="text-right">Retained total</th>
                              <th class="text-right">Retention factor</th>
                              <th>Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            <tr v-for="row in item.round_rows" :key="row.candidate_id">
                              <td>
                                <a v-if="row.candidate_username" :href="profileUrl(row.candidate_username)">{{ row.candidate_label }}</a>
                                <span v-else>{{ row.candidate_label }}</span>
                              </td>
                              <td class="text-right">{{ formatNumber4(row.retained_total) }}</td>
                              <td class="text-right">{{ formatNumber4(row.retention_factor) }}</td>
                              <td>
                                <span v-if="row.is_elected" class="badge badge-success">elected</span>
                                <span v-else-if="row.is_eliminated" class="badge badge-danger">eliminated</span>
                                <span v-else class="badge badge-secondary">continuing</span>
                              </td>
                            </tr>
                          </tbody>
                        </table>
                      </div>
                      <div v-if="item.event_type === 'tally_round' && item.audit_text" class="mb-0">
                        <template v-for="(line, lineIndex) in textLines(item.audit_text)" :key="`audit-${lineIndex}`">
                          {{ line }}<br v-if="lineIndex < textLines(item.audit_text).length - 1">
                        </template>
                      </div>
                      <ul v-if="item.event_type !== 'tally_completed' && item.elected_users && item.elected_users.length > 0" class="pl-3">
                        <li v-for="winner in item.elected_users" :key="winner.username">
                          {{ winner.full_name }} (<a :href="profileUrl(winner.username)">{{ winner.username }}</a>)
                        </li>
                      </ul>
                    </div>
                  </div>
                  </div>
                </template>
              </template>
              <p v-else class="text-muted mb-0">No audit events recorded.</p>

              <div>
                <i class="fas fa-clock bg-gray"></i>
              </div>
            </div>

            <div class="d-flex flex-wrap align-items-center justify-content-between mt-3">
              <a class="btn btn-link px-0" href="#timeline-top" title="Jump to the top of the timeline" @click.prevent="jumpToAnchor('timeline-top')">Back to top</a>

              <div v-if="pagination && (pagination.has_previous || pagination.has_next)" class="btn-group btn-group-sm" role="group" aria-label="Timeline navigation">
                <a v-if="pagination.has_previous" class="btn btn-outline-secondary" :href="pageUrl(pagination.previous_page_number)" title="Show newer timeline events" @click.prevent="onPageChange(pagination.previous_page_number)">Newer</a>
                <a v-if="pagination.has_next" class="btn btn-outline-secondary" :href="pageUrl(pagination.next_page_number)" title="Load older timeline events" @click.prevent="onPageChange(pagination.next_page_number)">Load older</a>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>