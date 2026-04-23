import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import MembershipStatsPage from "../MembershipStatsPage.vue";
import { readMembershipStatsBootstrap } from "../types";
import type { MembershipStatsBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const BOOTSTRAP: MembershipStatsBootstrap = {
  currentDays: "365",
  apiSummaryUrl: "/api/v1/stats/membership/summary",
  apiCompositionUrl: "/api/v1/stats/membership/charts/composition",
  apiTrendsUrl: "/api/v1/stats/membership/charts/trends",
  apiRetentionUrl: "/api/v1/stats/membership/charts/retention",
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
    membership_types: { labels: ["Individual"], counts: [87] },
    nationality_all_users: { labels: ["US", "DE"], counts: [80, 62] },
    nationality_active_members: { labels: ["US"], counts: [50] },
  },
};

const TRENDS_PAYLOAD = {
  generated_at: "2026-01-01T00:00:00+00:00",
  charts: {
    requests_trend: { labels: ["2025-12"], counts: [10] },
    decisions_trend: { labels: ["2025-12"], datasets: [{ label: "approved", data: [8] }] },
    expirations_upcoming: { labels: ["2026-02"], counts: [5] },
  },
};

const RETENTION_PAYLOAD = {
  generated_at: "2026-01-01T00:00:00+00:00",
  charts: {
    retention_cohorts_12m: {
      labels: ["2025-01"],
      retained: [5],
      lapsed_then_renewed: [2],
      lapsed_not_renewed: [1],
    },
  },
};

function makeSuccessfulFetch(): ReturnType<typeof vi.fn> {
  return vi.fn(async (url: string) => {
    if (String(url).includes("/summary")) {
      return new Response(JSON.stringify(SUMMARY_PAYLOAD));
    }
    if (String(url).includes("/composition")) {
      return new Response(JSON.stringify(COMPOSITION_PAYLOAD));
    }
    if (String(url).includes("/trends")) {
      return new Response(JSON.stringify(TRENDS_PAYLOAD));
    }
    if (String(url).includes("/retention")) {
      return new Response(JSON.stringify(RETENTION_PAYLOAD));
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

    const result = readMembershipStatsBootstrap(el);
    expect(result).not.toBeNull();
    expect(result?.currentDays).toBe("365");
    expect(result?.apiSummaryUrl).toBe("/s");
  });

  it("reads a valid days preset from the element dataset", () => {
    const el = document.createElement("div");
    el.dataset.membershipStatsCurrentDays = "90";
    el.dataset.membershipStatsApiSummaryUrl = "/s";
    el.dataset.membershipStatsApiCompositionUrl = "/c";
    el.dataset.membershipStatsApiTrendsUrl = "/t";
    el.dataset.membershipStatsApiRetentionUrl = "/r";

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

    mount(MembershipStatsPage, { props: { bootstrap: BOOTSTRAP } });

    await flushPromises();
    await flushPromises();

    expect(nestedChartCtor).toHaveBeenCalled();
  });
});
