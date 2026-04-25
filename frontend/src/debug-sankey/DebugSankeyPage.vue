<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";

import { buildSankeyChart } from "../shared/sankeyChart";
import { readJsonScript, type DebugSankeyBootstrap, type DebugSankeyFlow } from "./types";

const props = defineProps<{
  bootstrap: DebugSankeyBootstrap;
}>();

const flows = ref<DebugSankeyFlow[]>([]);
const electedNodes = ref<string[]>([]);
const eliminatedNodes = ref<string[]>([]);
const chartCanvas = ref<HTMLCanvasElement | null>(null);
let sankeyChart: { destroy?: () => void } | null = null;

type ChartConstructor = new (...args: unknown[]) => { destroy?: () => void };
type WindowWithChart = Window & typeof globalThis & { Chart?: ChartConstructor };
type GlobalWithChart = typeof globalThis & { Chart?: ChartConstructor };

function destroySankeyChart(): void {
  sankeyChart?.destroy?.();
  sankeyChart = null;
}

async function renderSankeyChart(): Promise<void> {
  destroySankeyChart();
  if (flows.value.length === 0) {
    return;
  }

  await nextTick();

  const chartFactory = (window as WindowWithChart).Chart ?? (globalThis as GlobalWithChart).Chart;
  const canvas = chartCanvas.value;
  if (!chartFactory || !canvas) {
    return;
  }

  sankeyChart = buildSankeyChart(chartFactory, canvas, flows.value, electedNodes.value, eliminatedNodes.value);
}

onMounted(() => {
  flows.value = readJsonScript<DebugSankeyFlow[]>(props.bootstrap.flowsJsonId, []);
  electedNodes.value = readJsonScript<string[]>(props.bootstrap.electedJsonId, []);
  eliminatedNodes.value = readJsonScript<string[]>(props.bootstrap.eliminatedJsonId, []);
  void renderSankeyChart();
});

onBeforeUnmount(() => {
  destroySankeyChart();
});
</script>

<template>
  <div data-debug-sankey-vue-root>
    <div v-if="flows.length > 0" class="chart mb-3">
      <canvas
        id="debug-sankey-chart"
        ref="chartCanvas"
        aria-label="Sankey debug view"
        role="img"
      ></canvas>
    </div>
    <p v-else class="text-muted mb-3">No Sankey data available.</p>
  </div>
</template>