import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";

import MembershipStatsPage from "../MembershipStatsPage.vue";
import { activeMembershipsChartsFromApi } from "../types";
import { compositionChartsFromApi } from "../types";
import { readMembershipStatsBootstrap } from "../types";
import { retentionChartsFromApi } from "../types";
import { trendsChartsFromApi } from "../types";
import type { MembershipStatsBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const BOOTSTRAP: MembershipStatsBootstrap = {
  currentDays: "365",
  apiSummaryUrl: "/api/v1/stats/membership/summary/detail",
  apiCompositionUrl: "/api/v1/stats/membership/charts/composition/detail",
  apiTrendsUrl: "/api/v1/stats/membership/charts/trends/detail",
  apiRetentionUrl: "/api/v1/stats/membership/charts/retention/detail",
  apiActiveMembershipsUrl: "/api/v1/stats/membership/charts/active-memberships/detail",
};

const SUMMARY_PAYLOAD = {
  generated_at: "2026-01-01T00:00:00+00:00",
  summary: {
    total_freeipa_users: 142,
    active_individual_memberships: 87,
    active_org_sponsorships: 12,
    pending_requests: 3,
    on_hold_requests: 1,
    expiring_soon_90_days: 5,
    approval_time: {
      mean_hours: 48,
      median_hours: 36,
      p90_hours: 96,
      sample_size: 20,
      outlier_cutoff_days: 90,
    },
    retention_cohort_12m: { cohorts: 10, users: 75 },
  },
};

const COMPOSITION_PAYLOAD = {
  generated_at: "2026-01-01T00:00:00+00:00",
  charts: {
    membership_types: [{ membership_type: { code: "individual", name: "Individual" }, count: 87 }],
    nationality_all_users: [{ country_code: "US", count: 80 }, { country_code: "DE", count: 62 }],
    nationality_active_members: [{ country_code: "US", count: 50 }],
  },
};

const TRENDS_PAYLOAD = {
  generated_at: "2026-01-01T00:00:00+00:00",
  period_bucket: "month",
  chart_period_buckets: {
    requests_trend: "month",
    decisions_trend: "month",
    expirations_upcoming: "month",
  },
  charts: {
    requests_trend: [{ period: "2025-12", period_start_ms: 1764547200000, count: 10 }],
    decisions_trend: [{ period: "2025-12", period_start_ms: 1764547200000, status: "approved", count: 8 }],
    expirations_upcoming: [{ period: "2026-02", period_start_ms: 1769904000000, count: 5 }],
  },
};

const RETENTION_PAYLOAD = {
  generated_at: "2026-01-01T00:00:00+00:00",
  period_bucket: "month",
  charts: {
    retention_cohorts_12m: [
      {
        cohort_month: "2025-01",
        period_start_ms: 1735689600000,
        cohort_size: 8,
        retained: 5,
        lapsed_then_renewed: 2,
        lapsed_not_renewed: 1,
      },
    ],
  },
};

const ACTIVE_MEMBERSHIPS_PAYLOAD = {
  generated_at: "2026-01-01T00:00:00+00:00",
  days_param: "365",
  period_bucket: "month",
  charts: {
    active_memberships_over_time: [
      { period: "2025-11", period_start_ms: 1761955200000, membership_type: { code: "individual", name: "Individual" }, count: 12 },
      { period: "2025-12", period_start_ms: 1764547200000, membership_type: { code: "individual", name: "Individual" }, count: 14 },
      { period: "2025-12", period_start_ms: 1764547200000, membership_type: { code: "sponsor-standard", name: "Sponsor Standard" }, count: 3 },
    ],
  },
};

function makeSuccessfulFetch({
  summaryPayload = SUMMARY_PAYLOAD,
  compositionPayload = COMPOSITION_PAYLOAD,
  trendsPayload = TRENDS_PAYLOAD,
  retentionPayload = RETENTION_PAYLOAD,
  activeMembershipsPayload = ACTIVE_MEMBERSHIPS_PAYLOAD,
}: {
  summaryPayload?: typeof SUMMARY_PAYLOAD;
  compositionPayload?: typeof COMPOSITION_PAYLOAD;
  trendsPayload?: typeof TRENDS_PAYLOAD;
  retentionPayload?: typeof RETENTION_PAYLOAD;
  activeMembershipsPayload?: typeof ACTIVE_MEMBERSHIPS_PAYLOAD;
} = {}) {
  return vi.fn(async (url: string) => {
    if (String(url).includes("/summary")) {
      return new Response(JSON.stringify(summaryPayload));
    }
    if (String(url).includes("/composition")) {
      return new Response(JSON.stringify(compositionPayload));
    }
    if (String(url).includes("/trends")) {
      return new Response(JSON.stringify(trendsPayload));
    }
    if (String(url).includes("/retention")) {
      return new Response(JSON.stringify(retentionPayload));
    }
    if (String(url).includes("/active-memberships")) {
      return new Response(JSON.stringify(activeMembershipsPayload));
    }
    return new Response("{}", { status: 404 });
  });
}

describe("readMembershipStatsBootstrap", () => {
  it("returns null when required URL attributes are missing", () => {
    const el = document.createElement("div");
    expect(readMembershipStatsBootstrap(el)).toBeNull();
  });

  it("reads all attributes and defaults days to 365 when not provided", () => {
    const el = document.createElement("div");
    el.dataset.membershipStatsApiSummaryUrl = "/s";
    el.dataset.membershipStatsApiCompositionUrl = "/c";
    el.dataset.membershipStatsApiTrendsUrl = "/t";
    el.dataset.membershipStatsApiRetentionUrl = "/r";
    el.dataset.membershipStatsApiActiveMembershipsUrl = "/a";

    const result = readMembershipStatsBootstrap(el);
    expect(result).not.toBeNull();
    expect(result?.currentDays).toBe("365");
    expect(result?.apiSummaryUrl).toBe("/s");
    expect(result).toMatchObject({ apiActiveMembershipsUrl: "/a" });
  });

  it("reads a valid days preset from the element dataset", () => {
    const el = document.createElement("div");
    el.dataset.membershipStatsCurrentDays = "90";
    el.dataset.membershipStatsApiSummaryUrl = "/s";
    el.dataset.membershipStatsApiCompositionUrl = "/c";
    el.dataset.membershipStatsApiTrendsUrl = "/t";
    el.dataset.membershipStatsApiRetentionUrl = "/r";
    el.dataset.membershipStatsApiActiveMembershipsUrl = "/a";

    const result = readMembershipStatsBootstrap(el);
    expect(result?.currentDays).toBe("90");
  });

  it("falls back to 365 for an unknown days value", () => {
    const el = document.createElement("div");
    el.dataset.membershipStatsCurrentDays = "invalid";
    el.dataset.membershipStatsApiSummaryUrl = "/s";
    el.dataset.membershipStatsApiCompositionUrl = "/c";
    el.dataset.membershipStatsApiTrendsUrl = "/t";
    el.dataset.membershipStatsApiRetentionUrl = "/r";
    el.dataset.membershipStatsApiActiveMembershipsUrl = "/a";

    const result = readMembershipStatsBootstrap(el);
    expect(result?.currentDays).toBe("365");
  });
});

describe("MembershipStatsPage", () => {
  beforeEach(() => {
    // Chart.js is not available in jsdom; stub it to avoid errors.
    vi.stubGlobal("Chart", vi.fn(() => ({ update: vi.fn(), destroy: vi.fn(), data: {} })));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows summary card values after data loads", async () => {
    vi.stubGlobal("fetch", makeSuccessfulFetch());

    const wrapper = mount(MembershipStatsPage, { props: { bootstrap: BOOTSTRAP } });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("142");
    expect(wrapper.text()).toContain("87");
    expect(wrapper.text()).toContain("12");
    expect(wrapper.text()).toContain("3");
  });

  it("shows the retention empty state copy instead of a blank chart shell", async () => {
    const chartCtor = vi.fn(() => ({ update: vi.fn(), destroy: vi.fn(), data: {} }));
    vi.stubGlobal("Chart", chartCtor);
    vi.stubGlobal(
      "fetch",
      makeSuccessfulFetch({
        retentionPayload: {
          generated_at: "2026-01-01T00:00:00+00:00",
          charts: {
            retention_cohorts_12m: [],
          },
        },
      }),
    );

    const wrapper = mount(MembershipStatsPage, { props: { bootstrap: BOOTSTRAP } });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Member Renewal by Join Month (12-Month Cohorts)");
    expect(wrapper.text()).toContain("Cohorts Tracked (12m)");
    expect(wrapper.text()).toContain("No join-month cohorts have reached the 12-month renewal window yet.");
    expect(wrapper.find("#retention-cohorts-chart").exists()).toBe(false);
    expect(chartCtor).toHaveBeenCalledTimes(7);
  });

  it("renders days filter buttons with the active preset highlighted", async () => {
    vi.stubGlobal("fetch", makeSuccessfulFetch());

    const wrapper = mount(MembershipStatsPage, { props: { bootstrap: BOOTSTRAP } });

    await flushPromises();
    await flushPromises();

    const buttons = wrapper.findAll("button");
    const activeButton = buttons.find((b) => b.attributes("aria-pressed") === "true");
    expect(activeButton?.text()).toBe("365 days");
  });

  it("re-fetches data when a different days preset is selected", async () => {
    const fetchMock = makeSuccessfulFetch();
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipStatsPage, { props: { bootstrap: BOOTSTRAP } });

    await flushPromises();
    await flushPromises();

    // Click the 30-day preset button
    const thirtyBtn = wrapper.findAll("button").find((b) => b.text() === "30 days");
    expect(thirtyBtn).toBeDefined();
    await thirtyBtn!.trigger("click");

    await flushPromises();
    await flushPromises();

    // Should have fetched with days=30
    const calls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(calls.some((url) => url.includes("days=30"))).toBe(true);
  });

  it("shows an error message when the API returns a non-OK response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 500 })),
    );

    const wrapper = mount(MembershipStatsPage, { props: { bootstrap: BOOTSTRAP } });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Failed to load statistics");
  });

  it("formats approval time hours correctly", async () => {
    vi.stubGlobal("fetch", makeSuccessfulFetch());

    const wrapper = mount(MembershipStatsPage, { props: { bootstrap: BOOTSTRAP } });

    await flushPromises();
    await flushPromises();

    // Legacy formatter renders 48h as 2.0 days
    expect(wrapper.text()).toContain("2.0 days");
  });

  it("renders N/A approval durations when sample_size is zero", async () => {
    const emptyApproval = {
      ...SUMMARY_PAYLOAD,
      summary: {
        ...SUMMARY_PAYLOAD.summary,
        approval_time: { mean_hours: null, median_hours: null, p90_hours: null, sample_size: 0, outlier_cutoff_days: 90 },
      },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).includes("/summary")) return new Response(JSON.stringify(emptyApproval));
        if (String(url).includes("/composition")) return new Response(JSON.stringify(COMPOSITION_PAYLOAD));
        if (String(url).includes("/trends")) return new Response(JSON.stringify(TRENDS_PAYLOAD));
        return new Response(JSON.stringify(RETENTION_PAYLOAD));
      }),
    );

    const wrapper = mount(MembershipStatsPage, { props: { bootstrap: BOOTSTRAP } });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("N/A");
  });

  it("supports Chart global exposed as an object with a Chart constructor", async () => {
    const nestedChartCtor = vi.fn(() => ({ update: vi.fn(), destroy: vi.fn(), data: {} }));
    vi.stubGlobal("Chart", { Chart: nestedChartCtor });
    vi.stubGlobal("fetch", makeSuccessfulFetch());

    const wrapper = mount(MembershipStatsPage, { props: { bootstrap: BOOTSTRAP } });

    await flushPromises();
    await flushPromises();
    await nextTick();

    expect(wrapper.find("#retention-cohorts-chart").exists()).toBe(true);

    expect(nestedChartCtor).toHaveBeenCalled();
  });

  it("shows doughnut tooltip percentages using only currently visible slices", async () => {
    const chartCtor = vi.fn(() => ({ update: vi.fn(), destroy: vi.fn(), data: {} }));
    vi.stubGlobal("Chart", chartCtor);
    vi.stubGlobal(
      "fetch",
      makeSuccessfulFetch({
        compositionPayload: {
          ...COMPOSITION_PAYLOAD,
          charts: {
            ...COMPOSITION_PAYLOAD.charts,
            membership_types: [
              { membership_type: { code: "individual", name: "Individual" }, count: 30 },
              { membership_type: { code: "sustaining", name: "Sustaining" }, count: 50 },
              { membership_type: { code: "student", name: "Student" }, count: 20 },
            ],
          },
        },
      }),
    );

    mount(MembershipStatsPage, { props: { bootstrap: BOOTSTRAP } });

    await flushPromises();
    await flushPromises();

    const membershipTypesConfig = chartCtor.mock.calls[0]?.[1];
    const tooltipLabel = membershipTypesConfig?.options?.plugins?.tooltip?.callbacks?.label;

    expect(tooltipLabel).toBeTypeOf("function");
    expect(
      tooltipLabel({
        label: "Individual",
        parsed: 30,
        dataIndex: 0,
        chart: {
          data: {
            labels: ["Individual", "Sustaining", "Student"],
            datasets: [{ data: [30, 50, 20] }],
          },
          getDataVisibility: (index: number) => index !== 2,
        },
      }),
    ).toBe("Individual: 30 (37.5%)");
  });

  it("registers autocolors and applies plugin plus shared-period tooltip settings to membership stats charts", async () => {
    const chartCtor = vi.fn(() => ({ update: vi.fn(), destroy: vi.fn(), data: {} }));
    const register = vi.fn();
    const autocolorsPlugin = { id: "autocolors" };
    vi.stubGlobal("Chart", Object.assign(chartCtor, { register }));
    Object.defineProperty(window, "chartjs-plugin-autocolors", {
      value: autocolorsPlugin,
      configurable: true,
    });
    vi.stubGlobal("fetch", makeSuccessfulFetch());

    mount(MembershipStatsPage, { props: { bootstrap: BOOTSTRAP } });

    await flushPromises();
    await flushPromises();

    expect(register).toHaveBeenCalledWith(autocolorsPlugin);

    for (const config of chartCtor.mock.calls.map((call) => call[1])) {
      expect(config?.options?.plugins?.autocolors).toBeTruthy();
    }

    const doughnutConfigs = chartCtor.mock.calls.map((call) => call[1]).filter((config) => config?.type === "doughnut");
    expect(doughnutConfigs.length).toBeGreaterThan(0);
    for (const config of doughnutConfigs) {
      expect(config?.options?.plugins?.autocolors).toMatchObject({ mode: "data" });
    }

    const requestsTrendConfig = chartCtor.mock.calls.find(
      (call) => call[1]?.data?.datasets?.[0]?.label === "Requests",
    )?.[1];
    const activeMembershipsConfig = chartCtor.mock.calls.find(
      (call) => call[1]?.data?.datasets?.[0]?.label === "Individual",
    )?.[1];
    const decisionsTrendConfig = chartCtor.mock.calls.find(
      (call) => call[1]?.type === "bar" && call[1]?.data?.datasets?.[0]?.label === "approved",
    )?.[1];

    expect(requestsTrendConfig?.options?.interaction).toMatchObject({ mode: "index", intersect: false });
    expect(activeMembershipsConfig?.options?.interaction).toMatchObject({ mode: "index", intersect: false });
    expect(decisionsTrendConfig?.options?.interaction).toMatchObject({ mode: "index", intersect: false });
  });

  it("renders the active memberships over time card, fetches its endpoint, and builds stacked filled datasets", async () => {
    const chartCtor = vi.fn(() => ({ update: vi.fn(), destroy: vi.fn(), data: {} }));
    vi.stubGlobal("Chart", chartCtor);
    const fetchMock = makeSuccessfulFetch();
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipStatsPage, {
      props: {
        bootstrap: {
          ...BOOTSTRAP,
          apiActiveMembershipsUrl: "/api/v1/stats/membership/charts/active-memberships/detail",
        } as MembershipStatsBootstrap,
      },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Active Memberships Over Time (by Membership Type)");
    expect(wrapper.text()).toContain(
      "Affects approval time cards, Requests Trend, Decision Outcomes, and Active Memberships Over Time.",
    );
    expect(fetchMock.mock.calls.map((call) => String(call[0]))).toContain(
      "/api/v1/stats/membership/charts/active-memberships/detail?days=365",
    );

    const activeMembershipsConfig = chartCtor.mock.calls.find(
      (call) => call[1]?.data?.datasets?.[0]?.label === "Individual",
    )?.[1];

    expect(activeMembershipsConfig?.type).toBe("line");
    expect(activeMembershipsConfig?.options?.scales?.x?.type).toBe("timestack");
    expect(activeMembershipsConfig?.options?.scales?.y?.stacked).toBe(true);
    expect(activeMembershipsConfig?.data?.labels).toBeUndefined();
    expect(activeMembershipsConfig?.data?.datasets).toEqual([
      expect.objectContaining({
        label: "Individual",
        data: [
          { x: 1761955200000, y: 12 },
          { x: 1764547200000, y: 14 },
        ],
        fill: true,
      }),
      expect.objectContaining({
        label: "Sponsor Standard",
        data: [
          { x: 1761955200000, y: 0 },
          { x: 1764547200000, y: 3 },
        ],
        fill: true,
      }),
    ]);
  });

  it("maps timestamped trend rows into sorted point datasets and preserves fixed decision statuses", () => {
    const charts = trendsChartsFromApi({
      requests_trend: [
        { period: "2026-02-10", period_start_ms: 1770681600000, count: 1 },
        { period: "2026-02-03", period_start_ms: 1770076800000, count: 3 },
      ],
      decisions_trend: [
        { period: "2026-02-10", period_start_ms: 1770681600000, status: "approved", count: 2 },
        { period: "2026-02-03", period_start_ms: 1770076800000, status: "rejected", count: 1 },
      ],
      expirations_upcoming: [
        { period: "2026-03", period_start_ms: 1772323200000, count: 5 },
      ],
    } as never, "week", {
      requests_trend: "week",
      decisions_trend: "week",
      expirations_upcoming: "day",
    }) as any;

    expect(charts.requests_trend.datasets).toEqual([
      {
        label: "Requests",
        data: [
          { x: 1770076800000, y: 3 },
          { x: 1770681600000, y: 1 },
        ],
      },
    ]);
    expect(charts.decisions_trend.datasets).toEqual([
      {
        label: "approved",
        data: [
          { x: 1770076800000, y: 0 },
          { x: 1770681600000, y: 2 },
        ],
      },
      {
        label: "rejected",
        data: [
          { x: 1770076800000, y: 1 },
          { x: 1770681600000, y: 0 },
        ],
      },
      {
        label: "ignored",
        data: [
          { x: 1770076800000, y: 0 },
          { x: 1770681600000, y: 0 },
        ],
      },
      {
        label: "rescinded",
        data: [
          { x: 1770076800000, y: 0 },
          { x: 1770681600000, y: 0 },
        ],
      },
    ]);
    expect(charts.expirations_upcoming.datasets).toEqual([
      { label: "Upcoming Expirations", data: [{ x: 1772323200000, y: 5 }] },
    ]);
    expect(charts.requests_trend.periodBucket).toBe("week");
    expect(charts.decisions_trend.periodBucket).toBe("week");
    expect(charts.expirations_upcoming.periodBucket).toBe("day");
  });

  it("maps timestamped active-membership rows into zero-filled point datasets", () => {
    const charts = activeMembershipsChartsFromApi({
      active_memberships_over_time: [
        { period: "2026-02-03", period_start_ms: 1770076800000, membership_type: { code: "individual", name: "Individual" }, count: 3 },
        { period: "2026-02-10", period_start_ms: 1770681600000, membership_type: { code: "individual", name: "Individual" }, count: 5 },
        { period: "2026-02-10", period_start_ms: 1770681600000, membership_type: { code: "sponsor", name: "Sponsor" }, count: 1 },
      ],
    } as never) as any;

    expect(charts.active_memberships_over_time.datasets).toEqual([
      {
        label: "Individual",
        data: [
          { x: 1770076800000, y: 3 },
          { x: 1770681600000, y: 5 },
        ],
      },
      {
        label: "Sponsor",
        data: [
          { x: 1770076800000, y: 0 },
          { x: 1770681600000, y: 1 },
        ],
      },
    ]);
  });

  it("renders timestamp-driven chart configs with timestack for line charts and time for bar charts", async () => {
    const chartCtor = vi.fn(() => ({ update: vi.fn(), destroy: vi.fn(), data: {} }));
    vi.stubGlobal("Chart", chartCtor);
    vi.stubGlobal("fetch", makeSuccessfulFetch());

    mount(MembershipStatsPage, { props: { bootstrap: BOOTSTRAP } });

    await flushPromises();
    await flushPromises();

    const requestsTrendConfig = chartCtor.mock.calls.find(
      (call) => call[1]?.data?.datasets?.[0]?.label === "Requests",
    )?.[1];
    const activeMembershipsConfig = chartCtor.mock.calls.find(
      (call) => call[1]?.data?.datasets?.[0]?.label === "Individual",
    )?.[1];
    const decisionsTrendConfig = chartCtor.mock.calls.find(
      (call) => call[1]?.type === "bar" && call[1]?.data?.datasets?.[0]?.label === "approved",
    )?.[1];
    const expirationsConfig = chartCtor.mock.calls.find(
      (call) => call[1]?.type === "bar" && call[1]?.data?.datasets?.[0]?.label === "Upcoming Expirations",
    )?.[1];
    const retentionConfig = chartCtor.mock.calls.filter((call) => call[1]?.type === "bar").at(-1)?.[1];

    expect(requestsTrendConfig?.options?.scales?.x?.type).toBe("timestack");
    expect(activeMembershipsConfig?.options?.scales?.x?.type).toBe("timestack");
    expect(decisionsTrendConfig?.options?.scales?.x?.type).toBe("time");
    expect(expirationsConfig?.options?.scales?.x?.type).toBe("time");
    expect(retentionConfig?.options?.scales?.x?.type).toBe("time");
    expect(requestsTrendConfig?.data?.labels).toBeUndefined();
    expect(activeMembershipsConfig?.data?.labels).toBeUndefined();
    expect(decisionsTrendConfig?.data?.labels).toBeUndefined();
    expect(expirationsConfig?.data?.labels).toBeUndefined();
    expect(retentionConfig?.data?.labels).toBeUndefined();
  });

  it("keeps Decision Outcomes, Upcoming Expirations, and Member Renewal by Join Month on the supported timestamp-aware bar scale path", async () => {
    const chartCtor = vi.fn(() => ({ update: vi.fn(), destroy: vi.fn(), data: {} }));
    vi.stubGlobal("Chart", chartCtor);
    vi.stubGlobal("fetch", makeSuccessfulFetch());

    const wrapper = mount(MembershipStatsPage, { props: { bootstrap: BOOTSTRAP } });

    await flushPromises();
    await flushPromises();
    await nextTick();

    expect(wrapper.find("#retention-cohorts-chart").exists()).toBe(true);
    expect(chartCtor.mock.calls.length).toBeGreaterThanOrEqual(8);

    const chartConfigForCanvas = (canvasId: string) =>
      chartCtor.mock.calls.find((call) => (call[0] as HTMLCanvasElement | undefined)?.id === canvasId)?.[1];

    const decisionsTrendConfig = chartConfigForCanvas("decisions-trend-chart");
    const expirationsConfig = chartConfigForCanvas("expirations-upcoming-chart");
    const retentionConfig = chartCtor.mock.calls.find(
      (call) => call[1]?.type === "bar" && call[1]?.data?.datasets?.length === 3,
    )?.[1];

    expect(decisionsTrendConfig?.type).toBe("bar");
    expect(decisionsTrendConfig?.options?.scales?.x).toMatchObject({ type: "time", stacked: true, time: { unit: "month" } });
    expect(decisionsTrendConfig?.data?.datasets?.map((dataset: { data: Array<{ x: number; y: number }> }) => dataset.data)).toEqual([
      [{ x: 1764547200000, y: 8 }],
      [{ x: 1764547200000, y: 0 }],
      [{ x: 1764547200000, y: 0 }],
      [{ x: 1764547200000, y: 0 }],
    ]);
    expect(decisionsTrendConfig?.data?.labels).toBeUndefined();

    expect(expirationsConfig?.type).toBe("bar");
    expect(expirationsConfig?.options?.scales?.x).toMatchObject({ type: "time", time: { unit: "month" } });
    expect(expirationsConfig?.data?.datasets).toEqual([
      expect.objectContaining({
        label: "Upcoming Expirations",
        data: [{ x: 1769904000000, y: 5 }],
      }),
    ]);
    expect(expirationsConfig?.data?.labels).toBeUndefined();

    expect(retentionConfig?.type).toBe("bar");
    expect(retentionConfig?.options?.scales?.x).toMatchObject({ type: "time", stacked: true, time: { unit: "month" } });
    expect(retentionConfig?.data?.datasets).toEqual([
      expect.objectContaining({ label: "Retained", data: [{ x: 1735689600000, y: 5 }] }),
      expect.objectContaining({ label: "Lapsed then renewed", data: [{ x: 1735689600000, y: 2 }] }),
      expect.objectContaining({ label: "Lapsed (not renewed)", data: [{ x: 1735689600000, y: 1 }] }),
    ]);
    expect(retentionConfig?.data?.labels).toBeUndefined();
  });

  it("preserves fixed decision statuses while zero-filling missing timestamp buckets", () => {
    const charts = trendsChartsFromApi({
      requests_trend: [],
      decisions_trend: [
        { period: "2025-12", period_start_ms: 1764547200000, status: "approved", count: 8 },
        { period: "2025-12", period_start_ms: 1764547200000, status: "rejected", count: 2 },
      ],
      expirations_upcoming: [],
    } as never) as any;

    expect(charts.decisions_trend.periodBucket).toBe("month");
    expect(charts.decisions_trend.datasets).toEqual([
      { label: "approved", data: [{ x: 1764547200000, y: 8 }] },
      { label: "rejected", data: [{ x: 1764547200000, y: 2 }] },
      { label: "ignored", data: [{ x: 1764547200000, y: 0 }] },
      { label: "rescinded", data: [{ x: 1764547200000, y: 0 }] },
    ]);
  });

  it("rebuilds composition charts from canonical row payloads", () => {
    const charts = compositionChartsFromApi({
      membership_types: [
        { membership_type: { code: "individual", name: "Individual" }, count: 87 },
        { membership_type: { code: "sustaining", name: "Sustaining" }, count: 12 },
      ],
      nationality_all_users: [
        { country_code: "US", count: 80 },
        { country_code: "DE", count: 62 },
      ],
      nationality_active_members: [
        { country_code: "US", count: 50 },
        { country_code: "DE", count: 20 },
      ],
    });

    expect(charts.membership_types).toEqual({
      labels: ["Individual", "Sustaining"],
      counts: [87, 12],
    });
    expect(charts.nationality_all_users).toEqual({
      labels: ["US", "DE"],
      counts: [80, 62],
    });
    expect(charts.nationality_active_members).toEqual({
      labels: ["US", "DE"],
      counts: [50, 20],
    });
  });

  it("rebuilds retention charts from canonical cohort rows", () => {
    const charts = retentionChartsFromApi({
      retention_cohorts_12m: [
        {
          cohort_month: "2025-01",
          period_start_ms: 1735689600000,
          cohort_size: 8,
          retained: 5,
          lapsed_then_renewed: 2,
          lapsed_not_renewed: 1,
        },
        {
          cohort_month: "2025-02",
          period_start_ms: 1738368000000,
          cohort_size: 6,
          retained: 4,
          lapsed_then_renewed: 1,
          lapsed_not_renewed: 1,
        },
      ],
    });

    expect(charts.retention_cohorts_12m).toEqual({
      periodBucket: "month",
      datasets: [
        {
          label: "Retained",
          data: [
            { x: 1735689600000, y: 5 },
            { x: 1738368000000, y: 4 },
          ],
        },
        {
          label: "Lapsed then renewed",
          data: [
            { x: 1735689600000, y: 2 },
            { x: 1738368000000, y: 1 },
          ],
        },
        {
          label: "Lapsed (not renewed)",
          data: [
            { x: 1735689600000, y: 1 },
            { x: 1738368000000, y: 1 },
          ],
        },
      ],
    });
  });
});
