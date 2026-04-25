import { afterEach, describe, expect, it, vi } from "vitest";

import { mountElectionsTurnoutReportPage } from "../../entrypoints/electionsTurnoutReport";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-elections-turnout-report-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountElectionsTurnoutReportPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts when required turnout report bootstrap data exists", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            rows: [],
            chart_data: { labels: [], count_turnout: [], weight_turnout: [] },
          }),
        ),
      ),
    );
    (window as Window & { Chart?: new (...args: unknown[]) => object }).Chart = vi.fn(() => ({ destroy: vi.fn() })) as never;

    const root = buildRoot({
      "data-elections-turnout-report-api-url": "/api/v1/elections/reports/turnout",
      "data-elections-turnout-report-elections-url": "/elections/",
    });

    const app = mountElectionsTurnoutReportPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-elections-turnout-report-vue-root]")).not.toBeNull();
  });

  it("does not mount when required turnout report bootstrap data is missing", () => {
    const root = buildRoot({});

    const app = mountElectionsTurnoutReportPage(root);

    expect(app).toBeNull();
    expect(root.innerHTML).toBe("");
  });
});