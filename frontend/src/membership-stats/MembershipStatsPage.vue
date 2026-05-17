<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from "vue";

import { useMembershipStats } from "./composables/useMembershipStats";
import type { DaysPreset } from "./types";
import { DAYS_PRESETS } from "./types";
import type { MembershipStatsBootstrap } from "./types";

const props = defineProps<{
  bootstrap: MembershipStatsBootstrap;
}>();

const stats = useMembershipStats(props.bootstrap);

// Canvas refs for all charts
const canvasMembershipTypes = ref<HTMLCanvasElement | null>(null);
const canvasNationalityAll = ref<HTMLCanvasElement | null>(null);
const canvasNationalityMembers = ref<HTMLCanvasElement | null>(null);
const canvasRequestsTrend = ref<HTMLCanvasElement | null>(null);
const canvasDecisionsTrend = ref<HTMLCanvasElement | null>(null);
const canvasExpirations = ref<HTMLCanvasElement | null>(null);
const canvasRetention = ref<HTMLCanvasElement | null>(null);
const canvasActiveMemberships = ref<HTMLCanvasElement | null>(null);

type ChartKey =
  | "membershipTypes"
  | "requestsTrend"
  | "activeMemberships"
  | "decisionsTrend"
  | "expirations"
  | "nationalityAll"
  | "nationalityMembers"
  | "retention";

type ChartRef = { instance: ChartInstance | null };
const charts: Record<ChartKey, ChartRef> = {
  membershipTypes: { instance: null },
  requestsTrend: { instance: null },
  activeMemberships: { instance: null },
  decisionsTrend: { instance: null },
  expirations: { instance: null },
  nationalityAll: { instance: null },
  nationalityMembers: { instance: null },
  retention: { instance: null },
};

const chartLoading = ref<Record<ChartKey, boolean>>({
  membershipTypes: true,
  requestsTrend: true,
  activeMemberships: true,
  decisionsTrend: true,
  expirations: true,
  nationalityAll: true,
  nationalityMembers: true,
  retention: true,
});

const SHARED_PERIOD_TOOLTIP_OPTIONS = { mode: "index", intersect: false } as const;

type PeriodBucket = "day" | "week" | "month";
type TimePoint = { x: number; y: number };
type TimeChartDataset = {
  label: string;
  data: TimePoint[];
  backgroundColor?: string | string[];
};

let autocolorsRegistered = false;

function destroyChart(key: ChartKey): void {
  charts[key].instance?.destroy();
  charts[key].instance = null;
}

function destroyAllCharts(): void {
  destroyChart("membershipTypes");
  destroyChart("requestsTrend");
  destroyChart("activeMemberships");
  destroyChart("decisionsTrend");
  destroyChart("expirations");
  destroyChart("nationalityAll");
  destroyChart("nationalityMembers");
  destroyChart("retention");
}

function resetChartLoading(): void {
  chartLoading.value = {
    membershipTypes: true,
    requestsTrend: true,
    activeMemberships: true,
    decisionsTrend: true,
    expirations: true,
    nationalityAll: true,
    nationalityMembers: true,
    retention: true,
  };
}

function hideChartLoading(key: ChartKey): void {
  chartLoading.value[key] = false;
}

function hideAllChartLoading(): void {
  hideChartLoading("membershipTypes");
  hideChartLoading("requestsTrend");
  hideChartLoading("activeMemberships");
  hideChartLoading("decisionsTrend");
  hideChartLoading("expirations");
  hideChartLoading("nationalityAll");
  hideChartLoading("nationalityMembers");
  hideChartLoading("retention");
}

function getCanvas(key: ChartKey): HTMLCanvasElement | null {
  const map: Record<ChartKey, HTMLCanvasElement | null> = {
    membershipTypes: canvasMembershipTypes.value,
    requestsTrend: canvasRequestsTrend.value,
    activeMemberships: canvasActiveMemberships.value,
    decisionsTrend: canvasDecisionsTrend.value,
    expirations: canvasExpirations.value,
    nationalityAll: canvasNationalityAll.value,
    nationalityMembers: canvasNationalityMembers.value,
    retention: canvasRetention.value,
  };
  return map[key] ?? null;
}

function resolveChartConstructor(): ChartConstructor | null {
  const chartGlobal = window.Chart as unknown;
  if (typeof chartGlobal === "function") {
    return chartGlobal as ChartConstructor;
  }

  if (
    chartGlobal &&
    typeof chartGlobal === "object" &&
    "Chart" in chartGlobal &&
    typeof (chartGlobal as { Chart: unknown }).Chart === "function"
  ) {
    return (chartGlobal as { Chart: ChartConstructor }).Chart;
  }

  return null;
}

function buildChart(key: ChartKey, config: ChartConfiguration): void {
  destroyChart(key);
  const canvas = getCanvas(key);
  if (!canvas) return;
  const ChartCtor = resolveChartConstructor();
  if (!ChartCtor) return;
  const autocolorsPlugin = window["chartjs-plugin-autocolors"];
  if (!autocolorsRegistered && autocolorsPlugin && typeof ChartCtor.register === "function") {
    ChartCtor.register(autocolorsPlugin);
    autocolorsRegistered = true;
  }
  // Chart.js is loaded as a global static asset
  charts[key].instance = new ChartCtor(canvas, config);
}

function renderChartSafely(key: ChartKey, renderFn: () => void): void {
  try {
    renderFn();
  } catch (error) {
    console.warn("[membership-stats] chart render failed:", key, error);
  } finally {
    hideChartLoading(key);
  }
}

function formatDoughnutTooltipLabel(context: {
  label?: string;
  parsed?: unknown;
  dataIndex: number;
  chart: {
    data?: {
      datasets?: Array<{
        data?: unknown[];
      }>;
    };
    getDataVisibility?: (index: number) => boolean;
  };
}): string {
  const label = context.label ?? "";
  const value = typeof context.parsed === "number" ? context.parsed : Number(context.parsed ?? 0);
  const dataset = context.chart.data?.datasets?.[0]?.data ?? [];
  let visibleTotal = 0;
  for (const [index, entry] of dataset.entries()) {
    if (context.chart.getDataVisibility && !context.chart.getDataVisibility(index)) {
      continue;
    }
    const numericEntry = typeof entry === "number" ? entry : Number(entry ?? 0);
    if (Number.isFinite(numericEntry)) {
      visibleTotal += numericEntry;
    }
  }
  const percentage = visibleTotal > 0 ? (value / visibleTotal) * 100 : 0;

  return `${label}: ${value} (${percentage.toFixed(1)}%)`;
}

function formatTimestampForBucket(timestamp: number, bucket: PeriodBucket): string {
  const dateTime = window.luxon?.DateTime?.fromMillis?.(timestamp);
  if (dateTime) {
    if (bucket === "month") {
      return dateTime.toFormat("yyyy-LL");
    }
    return dateTime.toFormat("yyyy-LL-dd");
  }

  const date = new Date(timestamp);
  if (bucket === "month") {
    return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
  }
  return date.toISOString().slice(0, 10);
}

function tooltipTitleForBucket(bucket: PeriodBucket) {
  return (items: Array<{ raw?: unknown; parsed?: { x?: number } }>): string => {
    const firstItem = items[0];
    const raw = firstItem?.raw;
    const rawX =
      raw && typeof raw === "object" && "x" in raw && typeof (raw as { x?: unknown }).x === "number"
        ? (raw as { x: number }).x
        : firstItem?.parsed?.x;

    return typeof rawX === "number" ? formatTimestampForBucket(rawX, bucket) : "";
  };
}

function renderDoughnut(key: ChartKey, labels: string[], data: number[], title: string): void {
  renderChartSafely(key, () => {
    buildChart(key, {
      type: "doughnut",
      data: {
        labels,
        datasets: [
          {
            label: title,
            data,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          autocolors: {
            enabled: true,
            mode: "data",
          },
          tooltip: {
            callbacks: {
              label: formatDoughnutTooltipLabel,
            },
          },
        },
      },
    });
  });
}

function renderTimestampLineDatasets(
  key: ChartKey,
  periodBucket: PeriodBucket,
  datasets: TimeChartDataset[],
  options: {
    stacked?: boolean;
    fill?: boolean;
  } = {},
): void {
  const { stacked = false, fill = false } = options;
  renderChartSafely(key, () => {
    buildChart(key, {
      type: "line",
      data: {
        datasets: datasets.map((dataset) => ({
          ...dataset,
          fill,
          tension: 0.2,
          pointRadius: 0,
        })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: SHARED_PERIOD_TOOLTIP_OPTIONS,
        plugins: {
          autocolors: {
            enabled: true,
          },
          tooltip: {
            ...SHARED_PERIOD_TOOLTIP_OPTIONS,
            callbacks: {
              title: tooltipTitleForBucket(periodBucket),
            },
          },
        },
        scales: {
          x: {
            type: "timestack",
            stacked,
          },
          y: {
            stacked,
            beginAtZero: true,
            ticks: { precision: 0 },
          },
        },
      },
    });
  });
}

function renderTimeLine(key: ChartKey, periodBucket: PeriodBucket, datasets: TimeChartDataset[]): void {
  renderTimestampLineDatasets(key, periodBucket, datasets, { fill: true });
}

function renderTimestampStackedBar(
  key: ChartKey,
  periodBucket: PeriodBucket,
  datasets: TimeChartDataset[],
): void {
  renderChartSafely(key, () => {
    buildChart(key, {
      type: "bar",
      data: {
        datasets,
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: SHARED_PERIOD_TOOLTIP_OPTIONS,
        plugins: {
          autocolors: {
            enabled: true,
          },
          tooltip: {
            ...SHARED_PERIOD_TOOLTIP_OPTIONS,
            callbacks: {
              title: tooltipTitleForBucket(periodBucket),
            },
          },
        },
        scales: {
          x: {
            type: "time",
            stacked: true,
            time: {
              unit: periodBucket,
            },
          },
          y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } },
        },
      },
    });
  });
}

function renderTimestampBar(key: ChartKey, periodBucket: PeriodBucket, datasets: TimeChartDataset[]): void {
  renderChartSafely(key, () => {
    buildChart(key, {
      type: "bar",
      data: {
        datasets,
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: SHARED_PERIOD_TOOLTIP_OPTIONS,
        plugins: {
          autocolors: {
            enabled: true,
          },
          tooltip: {
            ...SHARED_PERIOD_TOOLTIP_OPTIONS,
            callbacks: {
              title: tooltipTitleForBucket(periodBucket),
            },
          },
        },
        scales: {
          x: {
            type: "time",
            time: {
              unit: periodBucket,
            },
          },
          y: { beginAtZero: true, ticks: { precision: 0 } },
        },
      },
    });
  });
}

function renderCharts(): void {
  const { compositionCharts, trendsCharts, retentionCharts, activeMembershipsCharts } = stats;
  if (!compositionCharts.value || !trendsCharts.value || !retentionCharts.value || !activeMembershipsCharts.value) {
    return;
  }

  const types = compositionCharts.value.membership_types;
  renderDoughnut("membershipTypes", types.labels ?? [], types.counts ?? [], "Membership types");

  const requests = trendsCharts.value.requests_trend;
  renderTimeLine("requestsTrend", requests.periodBucket, requests.datasets ?? []);

  const activeMemberships = activeMembershipsCharts.value.active_memberships_over_time;
  renderTimestampLineDatasets("activeMemberships", activeMemberships.periodBucket, activeMemberships.datasets ?? [], {
    stacked: true,
    fill: true,
  });

  const decisions = trendsCharts.value.decisions_trend;
  renderTimestampStackedBar(
    "decisionsTrend",
    decisions.periodBucket,
    decisions.datasets ?? [],
  );

  const expirations = trendsCharts.value.expirations_upcoming;
  renderTimestampBar("expirations", expirations.periodBucket, expirations.datasets ?? []);

  const nationalityAll = compositionCharts.value.nationality_all_users;
  renderDoughnut(
    "nationalityAll",
    nationalityAll.labels ?? [],
    nationalityAll.counts ?? [],
    "Country code (all active FreeIPA users)",
  );

  const nationalityMembers = compositionCharts.value.nationality_active_members;
  renderDoughnut(
    "nationalityMembers",
    nationalityMembers.labels ?? [],
    nationalityMembers.counts ?? [],
    "Country code (active individual members)",
  );

  const retention = retentionCharts.value.retention_cohorts_12m;
  if ((retention.datasets[0]?.data.length ?? 0) === 0) {
    destroyChart("retention");
    hideChartLoading("retention");
    return;
  }

  renderTimestampStackedBar("retention", retention.periodBucket, retention.datasets ?? []);
}

watch(
  [stats.compositionCharts, stats.trendsCharts, stats.retentionCharts, stats.activeMembershipsCharts],
  async () => {
    await nextTick();
    renderCharts();
  },
  { flush: "post" },
);

watch(canvasRetention, async (canvas) => {
  if (!canvas || !stats.retentionCharts.value) {
    return;
  }

  await nextTick();
  renderCharts();
});

watch(stats.isLoading, (isLoading) => {
  if (isLoading) {
    resetChartLoading();
    destroyAllCharts();
  }
});

watch(stats.error, (error) => {
  if (error) {
    hideAllChartLoading();
  }
});

async function selectDays(days: DaysPreset): Promise<void> {
  await stats.load(days);
}

onMounted(async () => {
  await stats.load(stats.currentDays.value);
});

const DAYS_LABELS: Record<DaysPreset, string> = {
  "30": "30 days",
  "90": "90 days",
  "180": "180 days",
  "365": "365 days",
  all: "All time",
};

function formatHoursDuration(hours: number | null): string {
  if (hours === null || hours === undefined) return "N/A";

  const numericHours = Number(hours);
  if (!Number.isFinite(numericHours) || Number.isNaN(numericHours)) return "N/A";

  if (numericHours < 24) {
    const totalMinutes = Math.round(numericHours * 60);
    if (totalMinutes >= 24 * 60) {
      return `${(numericHours / 24).toFixed(1)} days`;
    }

    const wholeHours = Math.floor(totalMinutes / 60);
    const remainingMinutes = totalMinutes % 60;
    return `${wholeHours}h ${remainingMinutes}m`;
  }

  if (numericHours < 168) {
    return `${(numericHours / 24).toFixed(1)} days`;
  }

  return `${(numericHours / 168).toFixed(1)} weeks`;
}

function retentionTotal(values: Array<number | TimePoint> | undefined): number {
  if (!values) return 0;
  return values.reduce((acc, value) => acc + (typeof value === "number" ? value : value.y), 0);
}

function hasRetentionCohortRows(): boolean {
  return (stats.retentionCharts.value?.retention_cohorts_12m.datasets[0]?.data.length ?? 0) > 0;
}
</script>

<template>
  <div id="membership-stats-root" data-membership-stats-vue-root>
    <div class="row mb-3">
      <div class="col-12">
        <div class="d-flex align-items-baseline flex-wrap gap-2">
          <div class="btn-group" role="group" aria-label="Membership stats date range presets">
            <button
              v-for="preset in DAYS_PRESETS"
              :key="preset"
              type="button"
              class="btn"
              :class="stats.currentDays.value === preset ? 'btn-primary' : 'btn-outline-secondary'"
              :aria-pressed="stats.currentDays.value === preset"
              @click="selectDays(preset)"
            >
              {{ DAYS_LABELS[preset] }}
            </button>
          </div>
          <small class="text-muted ml-3">
            Affects approval time cards, Requests Trend, Decision Outcomes, and Active Memberships Over Time.
          </small>
        </div>
      </div>
    </div>

    <div v-if="stats.error.value" class="alert alert-danger" role="alert">
      {{ stats.error.value }}
    </div>

    <div class="row membership-stats-summary">
      <div class="col-12 col-sm-6 col-md-4 membership-stats-summary-col">
        <div class="info-box">
          <span class="info-box-icon bg-primary"><i class="fas fa-users"></i></span>
          <div class="info-box-content">
            <span class="info-box-text">Total FreeIPA Users</span>
            <span class="info-box-number" data-stat-key="total_freeipa_users">
              <span v-if="!stats.summary.value" class="spinner-border spinner-border-sm" role="status" aria-hidden="true" />
              <template v-else>{{ stats.summary.value.total_freeipa_users }}</template>
            </span>
          </div>
        </div>
      </div>

      <div class="col-12 col-sm-6 col-md-4 membership-stats-summary-col">
        <div class="info-box">
          <span class="info-box-icon bg-info"><i class="fas fa-id-card"></i></span>
          <div class="info-box-content">
            <span class="info-box-text">Active Individual Members</span>
            <span class="info-box-number" data-stat-key="active_individual_memberships">
              <span v-if="!stats.summary.value" class="spinner-border spinner-border-sm" role="status" aria-hidden="true" />
              <template v-else>{{ stats.summary.value.active_individual_memberships }}</template>
            </span>
          </div>
        </div>
      </div>

      <div class="col-12 col-sm-6 col-md-4 membership-stats-summary-col">
        <div class="info-box">
          <span class="info-box-icon bg-warning"><i class="fas fa-hourglass-half"></i></span>
          <div class="info-box-content">
            <span class="info-box-text">Pending Requests</span>
            <span class="info-box-number" data-stat-key="pending_requests">
              <span v-if="!stats.summary.value" class="spinner-border spinner-border-sm" role="status" aria-hidden="true" />
              <template v-else>{{ stats.summary.value.pending_requests }}</template>
            </span>
          </div>
        </div>
      </div>

      <div class="col-12 col-sm-6 col-md-4 membership-stats-summary-col">
        <div class="info-box">
          <span class="info-box-icon bg-secondary"><i class="fas fa-pause-circle"></i></span>
          <div class="info-box-content">
            <span class="info-box-text">On Hold Requests</span>
            <span class="info-box-number" data-stat-key="on_hold_requests">
              <span v-if="!stats.summary.value" class="spinner-border spinner-border-sm" role="status" aria-hidden="true" />
              <template v-else>{{ stats.summary.value.on_hold_requests }}</template>
            </span>
          </div>
        </div>
      </div>

      <div class="col-12 col-sm-6 col-md-4 membership-stats-summary-col">
        <div class="info-box">
          <span class="info-box-icon bg-danger"><i class="fas fa-calendar-times"></i></span>
          <div class="info-box-content">
            <span class="info-box-text">Expiring (≤ 90 days)</span>
            <span class="info-box-number" data-stat-key="expiring_soon_90_days">
              <span v-if="!stats.summary.value" class="spinner-border spinner-border-sm" role="status" aria-hidden="true" />
              <template v-else>{{ stats.summary.value.expiring_soon_90_days }}</template>
            </span>
          </div>
        </div>
      </div>
    </div>

    <div class="row">
      <div class="col-12 col-md-4">
        <div class="info-box">
          <span class="info-box-icon bg-success"><i class="fas fa-stopwatch"></i></span>
          <div class="info-box-content">
            <span class="info-box-text">
              Approval Time (Mean)<br />
              <small class="text-muted font-weight-normal">{{ stats.currentDays.value === 'all' ? 'All time' : `Last ${stats.currentDays.value} days` }}</small>
            </span>
            <span class="info-box-number" data-stat-key="approval_time_mean_hours">
              <span v-if="!stats.summary.value" class="spinner-border spinner-border-sm" role="status" aria-hidden="true" />
              <template v-else>{{ formatHoursDuration(stats.summary.value.approval_time.mean_hours) }}</template>
            </span>
          </div>
        </div>
      </div>

      <div class="col-12 col-md-4">
        <div class="info-box">
          <span class="info-box-icon bg-success"><i class="fas fa-ruler-combined"></i></span>
          <div class="info-box-content">
            <span class="info-box-text">
              Approval Time (Median)<br />
              <small class="text-muted font-weight-normal">{{ stats.currentDays.value === 'all' ? 'All time' : `Last ${stats.currentDays.value} days` }}</small>
            </span>
            <span class="info-box-number" data-stat-key="approval_time_median_hours">
              <span v-if="!stats.summary.value" class="spinner-border spinner-border-sm" role="status" aria-hidden="true" />
              <template v-else>{{ formatHoursDuration(stats.summary.value.approval_time.median_hours) }}</template>
            </span>
          </div>
        </div>
      </div>

      <div class="col-12 col-md-4">
        <div class="info-box">
          <span class="info-box-icon bg-success"><i class="fas fa-chart-line"></i></span>
          <div class="info-box-content">
            <span class="info-box-text">
              Approval Time (P90)<br />
              <small class="text-muted font-weight-normal">{{ stats.currentDays.value === 'all' ? 'All time' : `Last ${stats.currentDays.value} days` }}</small>
            </span>
            <span class="info-box-number" data-stat-key="approval_time_p90_hours">
              <span v-if="!stats.summary.value" class="spinner-border spinner-border-sm" role="status" aria-hidden="true" />
              <template v-else>{{ formatHoursDuration(stats.summary.value.approval_time.p90_hours) }}</template>
            </span>
          </div>
        </div>
      </div>
    </div>

    <div class="row">
      <div class="col-12 col-md-3">
        <div class="info-box">
          <span class="info-box-icon bg-primary"><i class="fas fa-layer-group"></i></span>
          <div class="info-box-content">
            <span class="info-box-text">Cohorts Tracked (12m)</span>
            <span class="info-box-number" data-stat-key="retention_cohorts_count">
              <span v-if="!stats.summary.value" class="spinner-border spinner-border-sm" role="status" aria-hidden="true" />
              <template v-else>{{ stats.summary.value.retention_cohort_12m.cohorts }}</template>
            </span>
          </div>
        </div>
      </div>

      <div class="col-12 col-md-3">
        <div class="info-box">
          <span class="info-box-icon bg-success"><i class="fas fa-user-check"></i></span>
          <div class="info-box-content">
            <span class="info-box-text">Retained</span>
            <span class="info-box-number" data-stat-key="retention_retained_count">
              <span v-if="!stats.retentionCharts.value" class="spinner-border spinner-border-sm" role="status" aria-hidden="true" />
              <template v-else>{{ retentionTotal(stats.retentionCharts.value.retention_cohorts_12m.datasets[0]?.data) }}</template>
            </span>
          </div>
        </div>
      </div>

      <div class="col-12 col-md-3">
        <div class="info-box">
          <span class="info-box-icon bg-warning"><i class="fas fa-user-clock"></i></span>
          <div class="info-box-content">
            <span class="info-box-text">Lapsed Then Renewed</span>
            <span class="info-box-number" data-stat-key="retention_lapsed_then_renewed_count">
              <span v-if="!stats.retentionCharts.value" class="spinner-border spinner-border-sm" role="status" aria-hidden="true" />
              <template v-else>{{ retentionTotal(stats.retentionCharts.value.retention_cohorts_12m.datasets[1]?.data) }}</template>
            </span>
          </div>
        </div>
      </div>

      <div class="col-12 col-md-3">
        <div class="info-box">
          <span class="info-box-icon bg-danger"><i class="fas fa-user-times"></i></span>
          <div class="info-box-content">
            <span class="info-box-text">Lapsed (Not Renewed)</span>
            <span class="info-box-number" data-stat-key="retention_lapsed_not_renewed_count">
              <span v-if="!stats.retentionCharts.value" class="spinner-border spinner-border-sm" role="status" aria-hidden="true" />
              <template v-else>{{ retentionTotal(stats.retentionCharts.value.retention_cohorts_12m.datasets[2]?.data) }}</template>
            </span>
          </div>
        </div>
      </div>
    </div>

    <div class="row">
      <div class="col-lg-6">
        <div class="card">
          <div class="card-header"><h3 class="card-title">Membership Types (Active)</h3></div>
          <div class="card-body" style="position: relative; min-height: 320px">
            <div v-if="chartLoading.membershipTypes" class="d-flex align-items-center justify-content-center" data-chart-loading="membership-types" style="position: absolute; inset: 0">
              <div class="text-center">
                <div class="spinner-border" role="status" aria-hidden="true" />
                <div class="mt-2 text-muted">Loading…</div>
              </div>
            </div>
            <canvas ref="canvasMembershipTypes" id="membership-types-chart" height="260" />
          </div>
        </div>
      </div>

      <div class="col-lg-6">
        <div class="card">
          <div class="card-header">
            <h3 class="card-title">Requests Trend</h3>
            <div class="card-tools">
              <span class="badge badge-secondary">{{ stats.currentDays.value === 'all' ? 'All time' : `Last ${stats.currentDays.value} days` }}</span>
            </div>
          </div>
          <div class="card-body" style="position: relative; min-height: 320px">
            <div v-if="chartLoading.requestsTrend" class="d-flex align-items-center justify-content-center" data-chart-loading="requests-trend" style="position: absolute; inset: 0">
              <div class="text-center">
                <div class="spinner-border" role="status" aria-hidden="true" />
                <div class="mt-2 text-muted">Loading…</div>
              </div>
            </div>
            <canvas ref="canvasRequestsTrend" id="requests-trend-chart" height="260" />
          </div>
        </div>
      </div>
    </div>

    <div class="row">
      <div class="col-12">
        <div class="card">
          <div class="card-header">
            <h3 class="card-title">Active Memberships Over Time (by Membership Type)</h3>
            <div class="card-tools">
              <span class="badge badge-secondary">{{ stats.currentDays.value === 'all' ? 'All time' : `Last ${stats.currentDays.value} days` }}</span>
            </div>
          </div>
          <div class="card-body" style="position: relative; min-height: 320px">
            <div v-if="chartLoading.activeMemberships" class="d-flex align-items-center justify-content-center" data-chart-loading="active-memberships" style="position: absolute; inset: 0">
              <div class="text-center">
                <div class="spinner-border" role="status" aria-hidden="true" />
                <div class="mt-2 text-muted">Loading…</div>
              </div>
            </div>
            <canvas ref="canvasActiveMemberships" id="active-memberships-over-time-chart" height="260" />
          </div>
        </div>
      </div>
    </div>

    <div class="row">
      <div class="col-lg-6">
        <div class="card">
          <div class="card-header">
            <h3 class="card-title">Decision Outcomes</h3>
            <div class="card-tools">
              <span class="badge badge-secondary">{{ stats.currentDays.value === 'all' ? 'All time' : `Last ${stats.currentDays.value} days` }}</span>
            </div>
          </div>
          <div class="card-body" style="position: relative; min-height: 320px">
            <div v-if="chartLoading.decisionsTrend" class="d-flex align-items-center justify-content-center" data-chart-loading="decisions-trend" style="position: absolute; inset: 0">
              <div class="text-center">
                <div class="spinner-border" role="status" aria-hidden="true" />
                <div class="mt-2 text-muted">Loading…</div>
              </div>
            </div>
            <canvas ref="canvasDecisionsTrend" id="decisions-trend-chart" height="260" />
          </div>
        </div>
      </div>

      <div class="col-lg-6">
        <div class="card">
          <div class="card-header"><h3 class="card-title">Upcoming Expirations</h3></div>
          <div class="card-body" style="position: relative; min-height: 320px">
            <div v-if="chartLoading.expirations" class="d-flex align-items-center justify-content-center" data-chart-loading="expirations-upcoming" style="position: absolute; inset: 0">
              <div class="text-center">
                <div class="spinner-border" role="status" aria-hidden="true" />
                <div class="mt-2 text-muted">Loading…</div>
              </div>
            </div>
            <canvas ref="canvasExpirations" id="expirations-upcoming-chart" height="260" />
          </div>
        </div>
      </div>
    </div>

    <div class="row">
      <div class="col-lg-6">
        <div class="card">
          <div class="card-header"><h3 class="card-title">Country Code Distribution (All Active FreeIPA Users)</h3></div>
          <div class="card-body" style="position: relative; min-height: 320px">
            <div v-if="chartLoading.nationalityAll" class="d-flex align-items-center justify-content-center" data-chart-loading="nationality-all-users" style="position: absolute; inset: 0">
              <div class="text-center">
                <div class="spinner-border" role="status" aria-hidden="true" />
                <div class="mt-2 text-muted">Loading…</div>
              </div>
            </div>
            <canvas ref="canvasNationalityAll" id="nationality-all-users-chart" height="260" />
          </div>
        </div>
      </div>

      <div class="col-lg-6">
        <div class="card">
          <div class="card-header"><h3 class="card-title">Country Code Distribution (Active Individual Members)</h3></div>
          <div class="card-body" style="position: relative; min-height: 320px">
            <div v-if="chartLoading.nationalityMembers" class="d-flex align-items-center justify-content-center" data-chart-loading="nationality-active-members" style="position: absolute; inset: 0">
              <div class="text-center">
                <div class="spinner-border" role="status" aria-hidden="true" />
                <div class="mt-2 text-muted">Loading…</div>
              </div>
            </div>
            <canvas ref="canvasNationalityMembers" id="nationality-active-members-chart" height="260" />
          </div>
        </div>
      </div>
    </div>

    <div class="row">
      <div class="col-12">
        <div class="card">
          <div class="card-header"><h3 class="card-title">Member Renewal by Join Month (12-Month Cohorts)</h3></div>
          <div class="card-body" style="position: relative; min-height: 320px">
            <div v-if="chartLoading.retention" class="d-flex align-items-center justify-content-center" data-chart-loading="retention-cohorts" style="position: absolute; inset: 0">
              <div class="text-center">
                <div class="spinner-border" role="status" aria-hidden="true" />
                <div class="mt-2 text-muted">Loading…</div>
              </div>
            </div>
            <div v-else-if="!hasRetentionCohortRows()" class="d-flex align-items-center justify-content-center h-100 text-center text-muted px-3">
              No join-month cohorts have reached the 12-month renewal window yet.
            </div>
            <canvas v-else ref="canvasRetention" id="retention-cohorts-chart" height="260" />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style>
/* Bootstrap grid has no 5-across column size; keep legacy 20% desktop summary columns. */
@media (min-width: 992px) {
  .membership-stats-summary .membership-stats-summary-col {
    flex: 0 0 20%;
    max-width: 20%;
  }
}

#membership-stats-root .info-box-content {
  min-width: 0;
}

/* Long labels should wrap instead of overflowing the card. */
#membership-stats-root .info-box-text {
  white-space: normal;
  overflow: visible;
  text-overflow: initial;
  overflow-wrap: anywhere;
  line-height: 1.25;
}
</style>
