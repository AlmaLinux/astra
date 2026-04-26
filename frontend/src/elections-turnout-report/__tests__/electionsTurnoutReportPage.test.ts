import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import ElectionsTurnoutReportPage from "../ElectionsTurnoutReportPage.vue";
import type { ElectionsTurnoutReportBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const bootstrap: ElectionsTurnoutReportBootstrap = {
  apiUrl: "/api/v1/elections/reports/turnout",
  electionsUrl: "/elections/",
  electionDetailUrlTemplate: "/elections/123456789/",
};

describe("ElectionsTurnoutReportPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads and renders turnout rows", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            rows: [
              {
                election: {
                  id: 1,
                  name: "Board election",
                  status: "open",
                  start_date: "2026-04-01",
                },
                eligible_count: 10,
                eligible_weight: 12,
                participating_count: 5,
                participating_weight: 6,
                turnout_count_pct: 50,
                turnout_weight_pct: 50,
                candidates_count: 4,
                seats: 2,
                contest_ratio: 2,
                credentials_issued: true,
              },
            ],
            chart_data: {
              labels: ["2026-04-01: Board election"],
              count_turnout: [50],
              weight_turnout: [50],
            },
          }),
        ),
      ),
    );
    (window as Window & { Chart?: new (...args: unknown[]) => object }).Chart = vi.fn(() => ({ destroy: vi.fn() })) as never;

    const wrapper = mount(ElectionsTurnoutReportPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Cross-election turnout comparison");
    expect(wrapper.text()).toContain("Board election");
    expect(wrapper.find('a[href="/elections/1/"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("50%");
  });
});