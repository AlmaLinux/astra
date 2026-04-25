export interface SankeyFlow {
  from: string;
  to: string;
  flow: number;
}

interface SankeyNodeInfo {
  roundName: string;
  candidate: string;
  isRoundNode: boolean;
}

interface SankeyNode {
  key: string;
  x: number;
  y: number;
  in?: number;
  out?: number;
}

interface SankeyChartInstance {
  canvas?: HTMLCanvasElement;
  scales?: {
    x?: { getPixelForValue(value: number): number };
    y?: { getPixelForValue(value: number): number };
  };
  getDatasetMeta?(index: number): {
    controller?: {
      _nodes?: Map<string, SankeyNode>;
      options?: { nodeWidth?: number };
    };
  };
  destroy?: () => void;
}

type ChartConstructor = new (...args: unknown[]) => SankeyChartInstance;

interface SankeyDatasetContext {
  dataset?: { data?: SankeyFlow[] };
  dataIndex?: number;
  raw?: Partial<SankeyFlow>;
}

const palette = [
  "#1f77b4",
  "#ff7f0e",
  "#2ca02c",
  "#d62728",
  "#9467bd",
  "#8c564b",
  "#e377c2",
  "#7f7f7f",
  "#bcbd22",
  "#17becf",
];

function roundIndex(label: string): number {
  const match = /^Round\s+(\d+)/.exec(label);
  if (!match) {
    return 0;
  }
  return Number.parseInt(match[1], 10) || 0;
}

function splitNode(nodeId: string): SankeyNodeInfo {
  const raw = String(nodeId || "");
  if (raw.startsWith("Round ")) {
    const parts = raw.split(" · ");
    if (parts.length >= 2) {
      return {
        roundName: parts[0] || "",
        candidate: parts.slice(1).join(" · "),
        isRoundNode: true,
      };
    }
    return {
      roundName: raw,
      candidate: raw,
      isRoundNode: true,
    };
  }
  return {
    roundName: "",
    candidate: raw,
    isRoundNode: false,
  };
}

function readFlowNode(context: SankeyDatasetContext, key: "from" | "to"): string {
  const indexedRow = context.dataset?.data?.[context.dataIndex ?? -1];
  const row = indexedRow ?? context.raw;
  return String(row?.[key] || "");
}

function nodeSize(node: SankeyNode): number {
  const incoming = typeof node.in === "number" ? node.in : 0;
  const outgoing = typeof node.out === "number" ? node.out : 0;
  return Math.max(incoming || outgoing, outgoing || incoming, 0);
}

function formatVotes(value: number): string {
  if (!Number.isFinite(value)) {
    return "";
  }
  const rounded = Math.round(value);
  if (Math.abs(value - rounded) < 1e-6) {
    return String(rounded);
  }
  return Number(value).toFixed(4);
}

function buildSankeyPresentation(flows: SankeyFlow[], electedNodes: string[], eliminatedNodes: string[]) {
  const roundNames = new Set<string>();
  const candidateNames = new Set<string>();
  const nodeIds = new Set<string>();

  for (const flow of flows) {
    const from = String(flow.from || "");
    const to = String(flow.to || "");
    const fromInfo = splitNode(from);
    const toInfo = splitNode(to);

    nodeIds.add(from);
    nodeIds.add(to);

    if (fromInfo.roundName) {
      roundNames.add(fromInfo.roundName);
    }
    if (toInfo.roundName) {
      roundNames.add(toInfo.roundName);
    }

    if (fromInfo.candidate && fromInfo.candidate !== "Voters") {
      candidateNames.add(fromInfo.candidate);
    }
    if (toInfo.candidate && toInfo.candidate !== "Voters") {
      candidateNames.add(toInfo.candidate);
    }
  }

  const priority: Record<string, number> = { Voters: 0 };
  for (const nodeId of nodeIds) {
    if (nodeId === "Voters") {
      continue;
    }
    const info = splitNode(nodeId);
    if (info.roundName) {
      priority[nodeId] = roundIndex(info.roundName);
    }
  }

  const colors: Record<string, string> = {};
  [...candidateNames].sort().forEach((label, index) => {
    colors[label] = palette[index % palette.length] || "#6c757d";
  });

  const electedSet = new Set(electedNodes.map((nodeId) => String(nodeId || "").trim()).filter(Boolean));
  const eliminatedSet = new Set(eliminatedNodes.map((nodeId) => String(nodeId || "").trim()).filter(Boolean));
  const candidateRounds: Record<string, { min: number; max: number }> = {};
  const firstElectedRound: Record<string, number> = {};

  for (const nodeId of nodeIds) {
    if (nodeId === "Voters") {
      continue;
    }
    const info = splitNode(nodeId);
    if (!info.roundName) {
      continue;
    }
    const index = roundIndex(info.roundName);
    if (!index) {
      continue;
    }
    const ranges = candidateRounds[info.candidate] || { min: index, max: index };
    ranges.min = Math.min(index, ranges.min);
    ranges.max = Math.max(index, ranges.max);
    candidateRounds[info.candidate] = ranges;
  }

  for (const nodeId of electedNodes) {
    const info = splitNode(String(nodeId || ""));
    if (!info.roundName) {
      continue;
    }
    const index = roundIndex(info.roundName);
    if (!index) {
      continue;
    }
    if (!firstElectedRound[info.candidate] || index < firstElectedRound[info.candidate]) {
      firstElectedRound[info.candidate] = index;
    }
  }

  const labels: Record<string, string> = {};
  for (const nodeId of nodeIds) {
    if (nodeId === "Voters") {
      labels[nodeId] = "AlmaLinux\nCommunity\nVoters";
      continue;
    }
    const info = splitNode(nodeId);
    const candidateLabel = info.candidate || nodeId;
    if (info.roundName) {
      const index = roundIndex(info.roundName);
      const ranges = candidateRounds[candidateLabel];
      const electedIndex = firstElectedRound[candidateLabel] || null;
      if (ranges && index !== ranges.min && index !== ranges.max && index !== electedIndex) {
        labels[nodeId] = "";
        continue;
      }
    }
    let prefix = "";
    if (eliminatedSet.has(nodeId)) {
      prefix = "X ";
    } else if (electedSet.has(nodeId)) {
      prefix = "✓ ";
    }
    labels[nodeId] = `${prefix}${candidateLabel}`;
  }

  return {
    labels,
    priority,
    colorForNode(nodeId: string): string {
      if (nodeId === "Voters") {
        return "#082336";
      }
      const info = splitNode(nodeId);
      return colors[info.candidate] || "#6c757d";
    },
  };
}

function attachNodeTooltip(chart: SankeyChartInstance, labels: Record<string, string>): (() => void) | null {
  const canvas = chart.canvas;
  const parent = canvas?.parentElement;
  if (!canvas || !parent || !chart.getDatasetMeta) {
    return null;
  }
  const tooltipCanvas = canvas;
  const tooltipParent = parent;

  if (window.getComputedStyle(tooltipParent).position === "static") {
    tooltipParent.style.position = "relative";
  }

  const tooltipElement = document.createElement("div");
  tooltipElement.style.position = "absolute";
  tooltipElement.style.pointerEvents = "none";
  tooltipElement.style.background = "rgba(17, 24, 39, 0.9)";
  tooltipElement.style.color = "#f9fafb";
  tooltipElement.style.padding = "6px 8px";
  tooltipElement.style.borderRadius = "6px";
  tooltipElement.style.fontSize = "12px";
  tooltipElement.style.lineHeight = "1.2";
  tooltipElement.style.boxShadow = "0 4px 12px rgba(0, 0, 0, 0.25)";
  tooltipElement.style.opacity = "0";
  tooltipElement.style.transition = "opacity 120ms ease";
  tooltipParent.appendChild(tooltipElement);

  function findNodeAt(x: number, y: number): SankeyNode | null {
    const meta = chart.getDatasetMeta?.(0);
    const nodes = meta?.controller?._nodes ? Array.from(meta.controller._nodes.values()) : [];
    const xScale = chart.scales?.x;
    const yScale = chart.scales?.y;
    if (!xScale || !yScale) {
      return null;
    }

    const nodeWidth = meta?.controller?.options?.nodeWidth || 10;
    for (const node of nodes) {
      const size = nodeSize(node);
      const xStart = xScale.getPixelForValue(node.x);
      const yStart = yScale.getPixelForValue(node.y);
      const height = Math.abs(yScale.getPixelForValue(node.y + size) - yStart);
      if (x >= xStart && x <= xStart + nodeWidth && y >= yStart && y <= yStart + height) {
        return node;
      }
    }
    return null;
  }

  function onMouseMove(event: MouseEvent): void {
    const rect = tooltipCanvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const node = findNodeAt(x, y);
    if (!node) {
      tooltipElement.style.opacity = "0";
      return;
    }

    const info = splitNode(node.key);
    const roundLabel = info.roundName ? `${info.roundName} · ` : "";
    const candidateLabel = labels[node.key] || info.candidate || node.key;
    const votes = formatVotes(nodeSize(node));
    tooltipElement.textContent = `${roundLabel}${candidateLabel}: ${votes} vote${votes === "1" ? "" : "s"}`;

    tooltipElement.style.opacity = "1";
    const offset = 12;
    const parentRect = tooltipParent.getBoundingClientRect();
    const tooltipWidth = tooltipElement.offsetWidth || 0;
    let left = x + offset;
    if (left + tooltipWidth > parentRect.width) {
      left = x - offset - tooltipWidth;
    }
    tooltipElement.style.left = `${Math.max(left, 0)}px`;
    tooltipElement.style.top = `${y + offset}px`;
  }

  function onMouseLeave(): void {
    tooltipElement.style.opacity = "0";
  }

  tooltipCanvas.addEventListener("mousemove", onMouseMove);
  tooltipCanvas.addEventListener("mouseleave", onMouseLeave);

  return () => {
    tooltipCanvas.removeEventListener("mousemove", onMouseMove);
    tooltipCanvas.removeEventListener("mouseleave", onMouseLeave);
    tooltipElement.remove();
  };
}

export function buildSankeyChart(
  chartFactory: ChartConstructor,
  canvas: HTMLCanvasElement,
  flows: SankeyFlow[],
  electedNodes: string[],
  eliminatedNodes: string[],
): SankeyChartInstance | null {
  const context = canvas.getContext("2d");
  if (!context) {
    return null;
  }

  const presentation = buildSankeyPresentation(flows, electedNodes, eliminatedNodes);
  const chart = new chartFactory(context, {
    type: "sankey",
    data: {
      datasets: [
        {
          data: flows,
          priority: presentation.priority,
          labels: presentation.labels,
          colorFrom: (context: SankeyDatasetContext) => presentation.colorForNode(readFlowNode(context, "from")),
          colorTo: (context: SankeyDatasetContext) => presentation.colorForNode(readFlowNode(context, "to")),
          colorMode: "gradient",
          borderWidth: 1,
          borderColor: "#111827",
        },
      ],
    },
    options: {
      plugins: {
        legend: {
          display: false,
        },
      },
    },
  });
  const cleanupTooltip = attachNodeTooltip(chart, presentation.labels);
  const destroyChart = chart.destroy?.bind(chart);
  chart.destroy = () => {
    cleanupTooltip?.();
    destroyChart?.();
  };
  return chart;
}