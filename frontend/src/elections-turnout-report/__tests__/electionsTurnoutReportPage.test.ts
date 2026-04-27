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
  apiUrl: "/api/v1/elections/reports/turnout/detail",
  electionsUrl: "/elections/",
  electionDetailUrlTemplate: "/elections/123456789/",
};

describe("ElectionsTurnoutReportPage", () => {
  afterEach(() => {
    delete (window as Window & { Chart?: unknown }).Chart;
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
                  start_datetime: "2026-04-01T10:00:00+00:00",
                },
                eligible_count: 10,
                eligible_weight: 12,
                participating_count: 5,
                participating_weight: 7,
                turnout_count_pct: 50,
                turnout_weight_pct: 58.33,
                candidates_count: 4,
                seats: 2,
                contest_ratio: 2,
                credentials_issued: true,
              },
              {
                election: {
                  id: 2,
                  name: "Council election",
                  status: "closed",
                  start_datetime: "2026-05-01T09:00:00+00:00",
                },
                eligible_count: 20,
                eligible_weight: 25,
                participating_count: 0,
                participating_weight: 0,
                turnout_count_pct: 0,
                turnout_weight_pct: 0,
                candidates_count: 4,
                seats: 2,
                contest_ratio: 2,
                credentials_issued: false,
              },
            ],
          }),
        ),
      ),
    );
    const chartMock = vi.fn(() => ({ destroy: vi.fn() }));
    (window as Window & { Chart?: new (...args: unknown[]) => object }).Chart = chartMock as never;

    const wrapper = mount(ElectionsTurnoutReportPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Cross-election turnout comparison");
    expect(wrapper.text()).toContain("Board election");
    expect(wrapper.text()).toContain("Council election");
    expect(wrapper.text()).toContain("2026-04-01");
    expect(wrapper.text()).toContain("2026-05-01");
    expect(wrapper.find('a[href="/elections/1/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/elections/2/"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("50%");
    expect(wrapper.text()).toContain("58.33%");
    expect(wrapper.text()).toContain("credentials not yet issued");
    expect(chartMock).toHaveBeenCalledOnce();
    expect(chartMock.mock.calls[0]?.[1]).toMatchObject({
      data: {
        labels: ["2026-04-01: Board election", "2026-05-01: Council election"],
        datasets: [
          expect.objectContaining({
            label: "Turnout % (count)",
            data: [50, 0],
          }),
          expect.objectContaining({
            label: "Turnout % (weight)",
            data: [58.33, 0],
          }),
        ],
      },
    });
  });

  it("renders the empty state and skips chart construction when no elections are available", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          rows: [],
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const chartMock = vi.fn(() => ({ destroy: vi.fn() }));
    (window as Window & { Chart?: new (...args: unknown[]) => object }).Chart = chartMock as never;

    const wrapper = mount(ElectionsTurnoutReportPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("No non-draft elections are available yet.");
    expect(wrapper.find("canvas").exists()).toBe(false);
    expect(chartMock).not.toHaveBeenCalled();
  });
});