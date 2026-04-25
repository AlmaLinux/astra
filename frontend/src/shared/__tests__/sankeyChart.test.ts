import { describe, expect, it, vi } from "vitest";

import { buildSankeyChart } from "../sankeyChart";

function makeCanvas(): HTMLCanvasElement {
  const parent = document.createElement("div");
  const canvas = document.createElement("canvas");
  parent.appendChild(canvas);
  document.body.appendChild(parent);
  return canvas;
}

describe("buildSankeyChart", () => {
  it("removes node tooltip listeners and DOM when the chart is destroyed", () => {
    const canvas = makeCanvas();
    vi.spyOn(canvas, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
    const removeEventListener = vi.spyOn(canvas, "removeEventListener");
    const chartDestroy = vi.fn();
    const chartFactory = vi.fn(() => ({
      canvas,
      destroy: chartDestroy,
      getDatasetMeta: () => ({ controller: { _nodes: new Map(), options: { nodeWidth: 10 } } }),
      scales: {
        x: { getPixelForValue: (value: number) => value },
        y: { getPixelForValue: (value: number) => value },
      },
    }));

    const chart = buildSankeyChart(
      chartFactory,
      canvas,
      [{ from: "Voters", to: "Round 1 · alice", flow: 1 }],
      ["Round 1 · alice"],
      [],
    );

    expect(canvas.parentElement?.querySelectorAll("div")).toHaveLength(1);

    chart?.destroy?.();

    expect(removeEventListener).toHaveBeenCalledWith("mousemove", expect.any(Function));
    expect(removeEventListener).toHaveBeenCalledWith("mouseleave", expect.any(Function));
    expect(chartDestroy).toHaveBeenCalledOnce();
    expect(canvas.parentElement?.querySelectorAll("div")).toHaveLength(0);
  });
});
