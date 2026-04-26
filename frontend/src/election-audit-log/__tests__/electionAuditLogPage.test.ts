import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import ElectionAuditLogPage from "../ElectionAuditLogPage.vue";
import type { ElectionAuditBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

function deferredResponse(): {
  promise: Promise<Response>;
  resolve: (response: Response) => void;
} {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((innerResolve) => {
    resolve = innerResolve;
  });
  return { promise, resolve };
}

const bootstrap: ElectionAuditBootstrap = {
  apiUrl: "/api/v1/elections/1/audit-log",
  summaryApiUrl: "/api/v1/elections/1/audit-summary",
  detailUrl: "/elections/1/",
  algorithmUrl: "/elections/algorithm/",
  publicBallotsUrl: "/elections/1/public/ballots.json",
  publicAuditUrl: "/elections/1/public/audit.json",
  userProfileUrlTemplate: "/user/__username__/",
  name: "Board election",
  status: "tallied",
  startDatetime: "2026-04-01T10:00:00+00:00",
  endDatetime: "2026-04-10T10:00:00+00:00",
  numberOfSeats: 2,
  algorithmName: "Meek STV",
  algorithmVersion: "1.0",
};

describe("ElectionAuditLogPage", () => {
  afterEach(() => {
    vi.useRealTimers();
    window.history.replaceState(null, "", "/elections/1/audit/");
    document.body.innerHTML = "";
    delete window.Chart;
    vi.restoreAllMocks();
  });

  it("loads and renders summary and timeline events", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request) => {
        if (String(url).includes("audit-summary")) {
          return new Response(
            JSON.stringify({
              summary: {
                ballots_cast: 12,
                votes_cast: 12,
                quota: 4.0,
                empty_seats: 0,
                tally_elected_users: [{ username: "alice", full_name: "Alice Example" }],
                sankey_flows: [],
                sankey_elected_nodes: [],
                sankey_eliminated_nodes: [],
              },
            }),
          );
        }
        return new Response(
          JSON.stringify({
            audit_log: {
              items: [
                {
                  timestamp: "2026-04-11T10:15:00+00:00",
                  event_type: "tally_completed",
                  title: "Tally completed",
                  icon: "fas fa-flag-checkered",
                  icon_bg: "bg-success",
                  anchor: "jump-tally-completed",
                  payload: {},
                  elected_users: [{ username: "alice", full_name: "Alice Example" }],
                },
                {
                  timestamp: "2026-04-10T10:00:00+00:00",
                  event_type: "election_started",
                  title: "Election started",
                  icon: "fas fa-play",
                  icon_bg: "bg-green",
                  anchor: null,
                  payload: {
                    genesis_chain_hash: "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                    candidates: [
                      {
                        id: 1,
                        freeipa_username: "alice",
                        tiebreak_uuid: "00000000-0000-0000-0000-000000000001",
                      },
                    ],
                  },
                },
                {
                  timestamp: "2026-04-10T09:00:00+00:00",
                  event_type: "ballots_submitted_summary",
                  title: "Ballots submitted",
                  icon: "fas fa-layer-group",
                  icon_bg: "bg-blue",
                  anchor: null,
                  payload: {},
                  ballots_count: 12,
                  ballot_entries: [{ timestamp: "2026-04-10T09:00:00+00:00", ballot_hash: "abcdef0123456789", supersedes_ballot_hash: null }],
                },
              ],
              pagination: { count: 2, page: 1, num_pages: 1, page_numbers: [1], has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 2 },
              jump_links: [{ anchor: "jump-tally-completed", label: "Results" }],
            },
          }),
        );
      }),
    );

    const wrapper = mount(ElectionAuditLogPage, {
      props: { bootstrap },
      attachTo: document.body,
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Board election");
    expect(wrapper.text()).toContain("Timeline");
    expect(wrapper.text()).toContain("Tally completed");
    expect(wrapper.text()).toContain("Genesis chain head:");
    expect(wrapper.text()).toContain("Candidates (tie-break order):");
    expect(wrapper.text()).toContain("00000000-0000-0000-0000-000000000001");
    expect(wrapper.text()).toContain("12 ballots submitted.");
    expect(wrapper.find('a[href="/elections/1/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/user/alice/"]').exists()).toBe(true);
  });

  it("renders the timeline before the summary request finishes", async () => {
    const summaryDeferred = deferredResponse();
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string | URL | Request) => {
        if (String(url).includes("audit-summary")) {
          return summaryDeferred.promise;
        }
        return Promise.resolve(
          new Response(
            JSON.stringify({
              audit_log: {
                items: [
                  {
                    timestamp: "2026-04-11T10:15:00+00:00",
                    event_type: "tally_completed",
                    title: "Tally completed",
                    icon: "fas fa-flag-checkered",
                    icon_bg: "bg-success",
                    anchor: "jump-tally-completed",
                    payload: {},
                    elected_users: [{ username: "alice", full_name: "Alice Example" }],
                  },
                ],
                pagination: {
                  count: 1,
                  page: 1,
                  num_pages: 1,
                  page_numbers: [1],
                  has_previous: false,
                  has_next: false,
                  previous_page_number: null,
                  next_page_number: null,
                  start_index: 1,
                  end_index: 1,
                },
                jump_links: [{ anchor: "jump-tally-completed", label: "Results" }],
              },
            }),
          ),
        );
      }),
    );

    const wrapper = mount(ElectionAuditLogPage, {
      props: { bootstrap },
      attachTo: document.body,
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Board election");
    expect(wrapper.text()).toContain("Timeline");
    expect(wrapper.text()).toContain("Tally completed");
    expect(wrapper.text()).not.toContain("Loading audit log...");
  });

  it("matches the legacy audit-log navigation, summary, and event detail UI", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request) => {
        if (String(url).includes("audit-summary")) {
          return new Response(
            JSON.stringify({
              summary: {
                ballots_cast: 2,
                votes_cast: 2,
                quota: 1.5,
                empty_seats: 1,
                tally_elected_users: [],
                sankey_flows: [],
                sankey_elected_nodes: [],
                sankey_eliminated_nodes: [],
              },
            }),
          );
        }
        return new Response(
          JSON.stringify({
            audit_log: {
              items: [
                {
                  timestamp: "2026-04-11T10:15:00+00:00",
                  event_type: "tally_round",
                  title: "Tally round 1",
                  icon: "fas fa-calculator",
                  icon_bg: "bg-info",
                  anchor: "jump-tally-rounds",
                  payload: {},
                  summary_text: "Alice met quota.\nTransfer next.",
                  audit_text: "Continuing count.\nAudit line.",
                  round_rows: [
                    {
                      candidate_id: 1,
                      candidate_username: "alice",
                      candidate_label: "alice",
                      retained_total: "4.0000",
                      retention_factor: "0.7500",
                      is_elected: true,
                      is_eliminated: false,
                    },
                  ],
                },
                {
                  timestamp: "2026-04-10T09:00:00+00:00",
                  event_type: "ballots_submitted_summary",
                  title: "Ballots submitted",
                  icon: "fas fa-layer-group",
                  icon_bg: "bg-blue",
                  anchor: null,
                  payload: {},
                  ballot_date: "2026-04-10",
                  ballots_count: 2,
                  first_timestamp: "2026-04-10T08:55:00+00:00",
                  last_timestamp: "2026-04-10T09:00:00+00:00",
                  ballots_preview_truncated: true,
                  ballots_preview_limit: 1,
                  ballot_entries: [
                    {
                      timestamp: "2026-04-10T09:00:00+00:00",
                      ballot_hash: "abcdef0123456789abcdef",
                      supersedes_ballot_hash: "fedcba9876543210fedcba",
                    },
                  ],
                },
              ],
              pagination: {
                count: 80,
                page: 2,
                num_pages: 3,
                page_numbers: [1, 2, 3],
                has_previous: true,
                has_next: true,
                previous_page_number: 1,
                next_page_number: 3,
                start_index: 61,
                end_index: 80,
              },
              jump_links: [{ anchor: "jump-tally-rounds", label: "Tally rounds" }],
            },
          }),
        );
      }),
    );

    const wrapper = mount(ElectionAuditLogPage, {
      props: { bootstrap },
      attachTo: document.body,
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("2026-04-01 10:00 UTC → 2026-04-10 10:00 UTC");
    expect(wrapper.find('a[href="/elections/algorithm/"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("No elected list was recorded.");
    expect(wrapper.find('a[href="/elections/1/audit/"]').text()).toBe("Newer");
    expect(wrapper.find('a[href="/elections/1/audit/?page=3"]').text()).toBe("Load older");
    expect(wrapper.text()).toContain("Retention factor");
    expect(wrapper.text()).toContain("0.7500");
    expect(wrapper.html()).toMatch(/Alice met quota\.<br[^>]*>Transfer next\./);
    expect(wrapper.html()).toMatch(/Continuing count\.<br[^>]*>Audit line\./);
    expect(wrapper.html().indexOf("Alice met quota.")).toBeLessThan(wrapper.html().indexOf("Retention factor"));
    expect(wrapper.html().indexOf("Continuing count.")).toBeGreaterThan(wrapper.html().indexOf("0.7500"));
    expect(wrapper.text()).toContain("Show ballot hashes");
    expect(wrapper.find('button[title="Copy full ballot hash"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("Showing first 1 ballot.");
    expect(wrapper.find('a[href="#timeline-top"]').text()).toBe("Back to top");
    expect(wrapper.find("i.fas.fa-clock.bg-gray").exists()).toBe(true);
  });

  it("renders the legacy tally-completed Sankey chart container when flow data exists", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
    const chartMock = vi.fn(() => ({ destroy: vi.fn() }));
    window.Chart = chartMock as never;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request) => {
        if (String(url).includes("audit-summary")) {
          return new Response(
            JSON.stringify({
              summary: {
                ballots_cast: 3,
                votes_cast: 3,
                quota: 2,
                empty_seats: 1,
                tally_elected_users: [{ username: "alice", full_name: "Alice Example" }],
                sankey_flows: [{ from: "Voters", to: "Round 1 · alice", flow: 3 }],
                sankey_elected_nodes: ["Round 1 · alice"],
                sankey_eliminated_nodes: [],
              },
            }),
          );
        }
        await new Promise((resolve) => {
          setTimeout(resolve, 0);
        });
        return new Response(
          JSON.stringify({
            audit_log: {
              items: [
                {
                  timestamp: "2026-04-11T10:15:00+00:00",
                  event_type: "tally_completed",
                  title: "Tally completed",
                  icon: "fas fa-flag-checkered",
                  icon_bg: "bg-success",
                  anchor: "jump-tally-completed",
                  payload: {},
                  elected_users: [{ username: "alice", full_name: "Alice Example" }],
                },
              ],
              pagination: { count: 1, page: 1, num_pages: 1, page_numbers: [1], has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 1 },
              jump_links: [{ anchor: "jump-tally-completed", label: "Results" }],
            },
          }),
        );
      }),
    );

    const wrapper = mount(ElectionAuditLogPage, {
      props: { bootstrap },
      attachTo: document.body,
    });

    await flushPromises();
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Elected:");
    expect(wrapper.text()).toContain("Empty seats: 1");
    expect(wrapper.find(".chart.election-sankey-chart").exists()).toBe(true);
    expect(wrapper.find("#tally-sankey-chart").exists()).toBe(true);
    expect(wrapper.find("#tally-sankey-chart").attributes()).toMatchObject({
      "data-sankey-chart": "",
      "aria-label": "Vote flow by round",
      role: "img",
    });
    expect(document.getElementById("tally-sankey-chart")).not.toBeNull();
    expect(chartMock).toHaveBeenCalledOnce();
    const chartCalls = chartMock.mock.calls as unknown[][];
    const chartConfig = chartCalls[0]?.[1] as {
      data: { datasets: Array<{ labels: Record<string, string>; priority: Record<string, number>; colorFrom: (context: unknown) => string; colorTo: (context: unknown) => string }> };
    };
    const dataset = chartConfig.data.datasets[0];
    expect(dataset.labels).toMatchObject({
      Voters: "AlmaLinux\nCommunity\nVoters",
      "Round 1 · alice": "✓ alice",
    });
    expect(dataset.priority).toMatchObject({ Voters: 0, "Round 1 · alice": 1 });
    expect(dataset.colorFrom({ raw: { from: "Voters" } })).toBe("#082336");
    expect(dataset.colorTo({ raw: { to: "Round 1 · alice" } })).toBe("#1f77b4");
  });

  it("uses hash jump links as local anchors without reloading audit data", async () => {
    const scrollIntoView = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request) => {
        if (String(url).includes("audit-summary")) {
          return new Response(
            JSON.stringify({
              summary: {
                ballots_cast: 0,
                votes_cast: 0,
                quota: null,
                empty_seats: 0,
                tally_elected_users: [],
                sankey_flows: [],
                sankey_elected_nodes: [],
                sankey_eliminated_nodes: [],
              },
            }),
          );
        }

        return new Response(
          JSON.stringify({
            audit_log: {
              items: [
                {
                  timestamp: "2026-04-11T10:15:00+00:00",
                  event_type: "tally_completed",
                  title: "Tally completed",
                  icon: "fas fa-flag-checkered",
                  icon_bg: "bg-success",
                  anchor: "jump-tally-completed",
                  payload: {},
                  elected_users: [],
                },
              ],
              pagination: { count: 1, page: 1, num_pages: 1, page_numbers: [1], has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 1 },
              jump_links: [{ anchor: "jump-tally-completed", label: "Results" }],
            },
          }),
        );
      }),
    );

    const wrapper = mount(ElectionAuditLogPage, {
      props: { bootstrap },
      attachTo: document.body,
    });

    await flushPromises();
    await flushPromises();

    const target = wrapper.get("#jump-tally-completed").element as HTMLElement;
    target.scrollIntoView = scrollIntoView;
    await wrapper.get('a[href="#jump-tally-completed"]').trigger("click");

    expect(fetch).toHaveBeenCalledTimes(2);
    expect(window.location.hash).toBe("#jump-tally-completed");
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "start", behavior: "smooth" });

    wrapper.unmount();
  });

  it("removes the popstate listener and cancels queued Sankey rendering on unmount", async () => {
    vi.useFakeTimers();
    const addEventListener = vi.spyOn(window, "addEventListener");
    const removeEventListener = vi.spyOn(window, "removeEventListener");
    const clearTimeout = vi.spyOn(window, "clearTimeout");
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
    const chartMock = vi.fn(() => ({ destroy: vi.fn() }));
    window.Chart = chartMock as never;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request) => {
        if (String(url).includes("audit-summary")) {
          return {
            ok: true,
            json: async () => ({
              summary: {
                ballots_cast: 3,
                votes_cast: 3,
                quota: 2,
                empty_seats: 0,
                tally_elected_users: [{ username: "alice", full_name: "Alice Example" }],
                sankey_flows: [{ from: "Voters", to: "Round 1 · alice", flow: 3 }],
                sankey_elected_nodes: ["Round 1 · alice"],
                sankey_eliminated_nodes: [],
              },
            }),
          } as Response;
        }
        return {
          ok: true,
          json: async () => ({
            audit_log: {
              items: [
                {
                  timestamp: "2026-04-11T10:15:00+00:00",
                  event_type: "tally_completed",
                  title: "Tally completed",
                  icon: "fas fa-flag-checkered",
                  icon_bg: "bg-success",
                  anchor: "jump-tally-completed",
                  payload: {},
                  elected_users: [{ username: "alice", full_name: "Alice Example" }],
                },
              ],
              pagination: { count: 1, page: 1, num_pages: 1, page_numbers: [1], has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 1 },
              jump_links: [{ anchor: "jump-tally-completed", label: "Results" }],
            },
          }),
        } as Response;
      }),
    );

    const wrapper = mount(ElectionAuditLogPage, {
      props: { bootstrap },
      attachTo: document.body,
    });

    for (let index = 0; index < 6; index += 1) {
      await Promise.resolve();
    }

    expect(vi.getTimerCount()).toBeGreaterThan(0);
    const popstateListener = addEventListener.mock.calls.find(([eventName]) => eventName === "popstate")?.[1];
    expect(popstateListener).toEqual(expect.any(Function));

    wrapper.unmount();

    expect(removeEventListener).toHaveBeenCalledWith("popstate", popstateListener);
    expect(clearTimeout).toHaveBeenCalled();
    vi.runOnlyPendingTimers();
    expect(chartMock).not.toHaveBeenCalled();
  });
});