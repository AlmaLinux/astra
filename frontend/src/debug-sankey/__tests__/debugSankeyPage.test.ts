import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import DebugSankeyPage from "../DebugSankeyPage.vue";
import type { DebugSankeyBootstrap } from "../types";

const bootstrap: DebugSankeyBootstrap = {
  flowsJsonId: "debug-sankey-data",
  electedJsonId: "debug-sankey-elected",
  eliminatedJsonId: "debug-sankey-eliminated",
};

function addJsonScript(id: string, payload: unknown): void {
  const script = document.createElement("script");
  script.id = id;
  script.type = "application/json";
  script.textContent = JSON.stringify(payload);
  document.body.appendChild(script);
}

describe("DebugSankeyPage", () => {
  afterEach(() => {
    document.body.replaceChildren();
    delete (window as Window & { Chart?: unknown }).Chart;
    vi.restoreAllMocks();
  });

  it("renders the debug sankey chart from JSON script bootstrap data", async () => {
    addJsonScript("debug-sankey-data", [{ from: "Voters", to: "Round 1 · Pear", flow: 8 }]);
    addJsonScript("debug-sankey-elected", ["Round 1 · Pear"]);
    addJsonScript("debug-sankey-eliminated", []);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
    const chartMock = vi.fn(() => ({ destroy: vi.fn() }));
    (window as Window & { Chart?: new (...args: unknown[]) => object }).Chart = chartMock as never;

    const wrapper = mount(DebugSankeyPage, {
      props: { bootstrap },
      attachTo: document.body,
    });

    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();

    expect(wrapper.find("#debug-sankey-chart").exists()).toBe(true);
    expect(wrapper.find("#debug-sankey-chart").attributes()).toMatchObject({
      "aria-label": "Sankey debug view",
      role: "img",
    });
    expect(chartMock).toHaveBeenCalledOnce();
    const chartCalls = chartMock.mock.calls as unknown[][];
    const chartConfig = chartCalls[0]?.[1] as {
      data: { datasets: Array<{ labels: Record<string, string>; priority: Record<string, number>; colorFrom: (context: unknown) => string; colorTo: (context: unknown) => string }> };
    };
    const dataset = chartConfig.data.datasets[0];
    expect(dataset.labels).toMatchObject({
      Voters: "AlmaLinux\nCommunity\nVoters",
      "Round 1 · Pear": "✓ Pear",
    });
    expect(dataset.priority).toMatchObject({ Voters: 0, "Round 1 · Pear": 1 });
    expect(dataset.colorFrom({ raw: { from: "Voters" } })).toBe("#082336");
    expect(dataset.colorTo({ raw: { to: "Round 1 · Pear" } })).toBe("#1f77b4");
  });
});