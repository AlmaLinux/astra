<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, useTemplateRef } from "vue";

import type { ElectionsTurnoutReportBootstrap, ElectionsTurnoutReportResponse, ElectionsTurnoutReportRow } from "./types";
import { fillUrlTemplate } from "../shared/urlTemplates";

const props = defineProps<{
  bootstrap: ElectionsTurnoutReportBootstrap;
}>();

const rows = ref<ElectionsTurnoutReportRow[]>([]);
const chartData = ref<ElectionsTurnoutReportResponse["chart_data"] | null>(null);
const isLoading = ref(false);
const error = ref("");
const chartCanvas = useTemplateRef<HTMLCanvasElement>("chartCanvas");

let chartInstance: { destroy?: () => void } | null = null;

function credentialsText(row: ElectionsTurnoutReportRow, value: number): string {
  return row.credentials_issued ? `${value}%` : "";
}

function electionDetailUrl(electionId: number): string {
  return fillUrlTemplate(props.bootstrap.electionDetailUrlTemplate, "123456789", electionId);
}

function destroyChart(): void {
  chartInstance?.destroy?.();
  chartInstance = null;
}

function renderChart(): void {
  destroyChart();
  const payload = chartData.value;
  const canvas = chartCanvas.value;
  const chartFactory = (window as Window & { Chart?: new (...args: unknown[]) => { destroy?: () => void } }).Chart;
  if (!payload || !canvas || !chartFactory || payload.labels.length === 0) {
    return;
  }

  const context = canvas.getContext("2d");
  if (!context) {
    return;
  }

  chartInstance = new chartFactory(context, {
    type: "bar",
    data: {
      labels: payload.labels,
      datasets: [
        {
          label: "Turnout % (count)",
          data: payload.count_turnout,
          backgroundColor: "rgba(54, 162, 235, 0.7)",
          borderColor: "rgba(54, 162, 235, 1)",
          borderWidth: 1,
        },
        {
          label: "Turnout % (weight)",
          data: payload.weight_turnout,
          backgroundColor: "rgba(255, 159, 64, 0.7)",
          borderColor: "rgba(255, 159, 64, 1)",
          borderWidth: 1,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          beginAtZero: true,
          max: 100,
          ticks: {
            callback(value: number | string) {
              return `${value}%`;
            },
          },
        },
      },
      plugins: {
        tooltip: {
          callbacks: {
            label(context: { dataset: { label: string }; parsed: { y: number } }) {
              return `${context.dataset.label}: ${context.parsed.y}%`;
            },
          },
        },
      },
    },
  });
}

async function load(): Promise<void> {
  isLoading.value = true;
  error.value = "";

  try {
    const response = await fetch(props.bootstrap.apiUrl, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!response.ok) {
      error.value = "Unable to load the turnout report right now.";
      return;
    }

    const payload = (await response.json()) as ElectionsTurnoutReportResponse;
    rows.value = payload.rows;
    chartData.value = payload.chart_data;
    await nextTick();
    renderChart();
  } catch {
    error.value = "Unable to load the turnout report right now.";
  } finally {
    isLoading.value = false;
  }
}

onMounted(async () => {
  await load();
});

onBeforeUnmount(() => {
  destroyChart();
});
</script>

<template>
  <div data-elections-turnout-report-vue-root>
    <div v-if="error" class="alert alert-danger">{{ error }}</div>
    <div v-else>
      <div class="row">
        <div class="col-12">
          <div class="card card-outline card-primary">
            <div class="card-header">
              <h3 class="card-title">Cross-election turnout comparison</h3>
            </div>
            <div class="card-body p-0">
              <div v-if="isLoading" class="p-3 text-muted">Loading turnout report...</div>
              <div v-else class="table-responsive">
                <table class="table table-striped table-hover mb-0">
                  <thead>
                    <tr>
                      <th>Election</th>
                      <th>Start date</th>
                      <th>Status</th>
                      <th class="text-right">Eligible voters</th>
                      <th class="text-right">Eligible weight</th>
                      <th class="text-right">Participating voters</th>
                      <th class="text-right">Participating weight</th>
                      <th class="text-right">Turnout % (count)</th>
                      <th class="text-right">Turnout % (weight)</th>
                      <th class="text-right">Candidates / seats</th>
                      <th class="text-right">Contest ratio</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in rows" :key="row.election.id">
                      <td><a :href="electionDetailUrl(row.election.id)">{{ row.election.name }}</a></td>
                      <td>{{ row.election.start_date }}</td>
                      <td>{{ row.election.status }}</td>
                      <td class="text-right">{{ row.eligible_count }}</td>
                      <td class="text-right">{{ row.eligible_weight }}</td>
                      <td class="text-right">{{ row.participating_count }}</td>
                      <td class="text-right">{{ row.participating_weight }}</td>
                      <td class="text-right">
                        <template v-if="row.credentials_issued">{{ credentialsText(row, row.turnout_count_pct) }}</template>
                        <span v-else class="text-muted">credentials not yet issued</span>
                      </td>
                      <td class="text-right">
                        <template v-if="row.credentials_issued">{{ credentialsText(row, row.turnout_weight_pct) }}</template>
                        <span v-else class="text-muted">credentials not yet issued</span>
                      </td>
                      <td class="text-right">{{ row.candidates_count }} / {{ row.seats }}</td>
                      <td class="text-right">{{ row.contest_ratio }}</td>
                    </tr>
                    <tr v-if="rows.length === 0">
                      <td colspan="11" class="text-center text-muted">No non-draft elections are available yet.</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div v-if="rows.length > 0" class="row">
        <div class="col-12">
          <div class="card card-outline card-secondary">
            <div class="card-header">
              <h3 class="card-title">Turnout trend by election</h3>
            </div>
            <div class="card-body">
              <div class="chart">
                <canvas ref="chartCanvas" style="min-height: 320px; height: 320px; max-height: 320px; max-width: 100%;"></canvas>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>