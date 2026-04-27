import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import ElectionDetailSummaryPage from "../ElectionDetailSummaryPage.vue";
import type { ElectionDetailBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const bootstrap: ElectionDetailBootstrap = {
  infoApiUrl: "/api/v1/elections/1/detail",
  candidatesApiUrl: "/api/v1/elections/1/candidates",
  userProfileUrlTemplate: "/user/__username__/",
};

describe("ElectionDetailSummaryPage", () => {
  afterEach(() => {
    delete (window as Window & { Chart?: unknown }).Chart;
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("loads and renders summary, winners, and candidates", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
    const chartMock = vi.fn(() => ({ destroy: vi.fn() }));
    (window as Window & { Chart?: new (...args: unknown[]) => object }).Chart = chartMock as never;

    const fetchMock = vi.fn(async (input) => {
      const url = String(input);
      if (url.includes("/detail")) {
        return new Response(
          JSON.stringify({
            election: {
              id: 1,
              name: "Board election",
              description: "Foundation board",
              url: "https://example.org/elections/board",
              status: "tallied",
              start_datetime: "2026-04-01T10:00:00+00:00",
              end_datetime: "2026-04-10T10:00:00+00:00",
              number_of_seats: 2,
              quorum: 10,
              eligible_group_cn: "board-voters",
              can_vote: false,
              credential_issued_at: null,
              show_turnout_chart: true,
              turnout_stats: {
                participating_voter_count: 12,
                participating_vote_weight_total: 12,
                eligible_voter_count: 20,
                eligible_vote_weight_total: 20,
                required_participating_voter_count: 2,
                required_participating_vote_weight_total: 2,
                quorum_met: true,
                quorum_percent: 10,
                quorum_required: true,
                participating_voter_percent: 60,
                participating_vote_weight_percent: 60,
              },
              turnout_rows: [
                { day: "2026-04-01", count: 2 },
                { day: "2026-04-03", count: 5 },
              ],
              exclusion_groups: [
                {
                  name: "Employees",
                  max_elected: 1,
                  candidates: [{ username: "alice", full_name: "Alice Example" }],
                },
              ],
              election_is_finished: true,
              tally_winners: [{ username: "alice", full_name: "Alice Example" }],
              empty_seats: 1,
            },
          }),
        );
      }
      return new Response(
        JSON.stringify({
          candidates: {
            items: [
              {
                id: 1,
                username: "alice",
                has_user: true,
                full_name: "Alice Example",
                avatar_url: "/avatars/alice.png",
                description: "Experienced candidate",
                url: "https://example.org/alice",
                nominated_by: "bob",
                nominator_display_name: "Bob Example",
                nominator_profile_username: "bob",
              },
            ],
            pagination: {
              count: 1,
              page: 1,
              num_pages: 1,
              page_numbers: [1],
              show_first: false,
              show_last: false,
              has_previous: false,
              has_next: false,
              previous_page_number: null,
              next_page_number: null,
              start_index: 1,
              end_index: 1,
            },
          },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ElectionDetailSummaryPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain("Status");
    expect(wrapper.text()).toContain("Tallied");
    expect(wrapper.text()).toContain("2026-04-01 10:00 UTC → 2026-04-10 10:00 UTC");
    expect(wrapper.text()).toContain("Results");
    expect(wrapper.text()).toContain("Alice Example");
    expect(wrapper.text()).toContain("Experienced candidate");
    expect(wrapper.text()).toContain("Bob Example");
    expect(wrapper.text()).toContain("Empty seats: 1");
    expect(wrapper.text()).toContain("Ballots submitted over time");
    expect(wrapper.text()).toContain(
      "Alice Example (alice) belong to the Employees exclusion group: only 1 candidate of the group can be elected.",
    );
    expect(wrapper.find('a[href="/user/alice/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/user/bob/"]').exists()).toBe(true);
    expect(wrapper.find("canvas").exists()).toBe(true);
    expect(chartMock).toHaveBeenCalledOnce();
    expect(chartMock).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        data: expect.objectContaining({
          labels: [
            "2026-04-01",
            "2026-04-02",
            "2026-04-03",
            "2026-04-04",
            "2026-04-05",
            "2026-04-06",
            "2026-04-07",
            "2026-04-08",
            "2026-04-09",
            "2026-04-10",
          ],
          datasets: [
            expect.objectContaining({
              data: [2, 0, 5, 0, 0, 0, 0, 0, 0, 0],
            }),
          ],
        }),
      }),
    );
    expect(wrapper.find("table").exists()).toBe(false);
    expect(wrapper.find(".card.card-primary.h-100").exists()).toBe(true);
    expect(wrapper.find('img.img-circle[src="/avatars/alice.png"]').exists()).toBe(true);
    expect(wrapper.find("hr.candidate-card-divider").exists()).toBe(true);
  });

  it("renders a zero-filled turnout chart across the election range when turnout rows are empty", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
    const chartMock = vi.fn(() => ({ destroy: vi.fn() }));
    (window as Window & { Chart?: new (...args: unknown[]) => object }).Chart = chartMock as never;

    const fetchMock = vi.fn(async (input) => {
      const url = String(input);
      if (url.includes("/detail")) {
        return new Response(
          JSON.stringify({
            election: {
              id: 1,
              name: "Board election",
              description: "Foundation board",
              url: "https://example.org/elections/board",
              status: "tallied",
              start_datetime: "2026-04-01T10:00:00+00:00",
              end_datetime: "2026-04-03T10:00:00+00:00",
              viewer_timezone: "UTC",
              number_of_seats: 2,
              quorum: 10,
              eligible_group_cn: "board-voters",
              can_vote: false,
              viewer_email: null,
              credential_issued_at: null,
              eligibility_min_membership_age_days: 0,
              show_turnout_chart: true,
              turnout_stats: {
                participating_voter_count: 0,
                participating_vote_weight_total: 0,
                eligible_voter_count: 20,
                eligible_vote_weight_total: 20,
                required_participating_voter_count: 2,
                required_participating_vote_weight_total: 2,
                quorum_met: false,
                quorum_percent: 10,
                quorum_required: true,
                participating_voter_percent: 0,
                participating_vote_weight_percent: 0,
              },
              turnout_rows: [],
              exclusion_groups: [],
              election_is_finished: true,
              tally_winners: [],
              empty_seats: 0,
            },
          }),
        );
      }
      return new Response(
        JSON.stringify({
          candidates: {
            items: [],
            pagination: {
              count: 0,
              page: 1,
              num_pages: 1,
              page_numbers: [1],
              show_first: false,
              show_last: false,
              has_previous: false,
              has_next: false,
              previous_page_number: null,
              next_page_number: null,
              start_index: 0,
              end_index: 0,
            },
          },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    mount(ElectionDetailSummaryPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(chartMock).toHaveBeenCalledOnce();
    expect(chartMock).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        data: expect.objectContaining({
          labels: ["2026-04-01", "2026-04-02", "2026-04-03"],
          datasets: [
            expect.objectContaining({
              data: [0, 0, 0],
            }),
          ],
        }),
      }),
    );
  });

  it("uses the viewer timezone and today boundary for open-election zero-ballot charts", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-05T01:30:00Z"));
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
    const chartMock = vi.fn(() => ({ destroy: vi.fn() }));
    (window as Window & { Chart?: new (...args: unknown[]) => object }).Chart = chartMock as never;

    const fetchMock = vi.fn(async (input) => {
      const url = String(input);
      if (url.includes("/detail")) {
        return new Response(
          JSON.stringify({
            election: {
              id: 1,
              name: "Open election",
              description: "Still in progress",
              url: "https://example.org/elections/open",
              status: "open",
              start_datetime: "2026-04-02T12:00:00+00:00",
              end_datetime: "2026-04-10T12:00:00+00:00",
              viewer_timezone: "America/Los_Angeles",
              number_of_seats: 1,
              quorum: 10,
              eligible_group_cn: "board-voters",
              can_vote: false,
              viewer_email: null,
              credential_issued_at: null,
              eligibility_min_membership_age_days: 0,
              show_turnout_chart: true,
              turnout_stats: {
                participating_voter_count: 0,
                participating_vote_weight_total: 0,
                eligible_voter_count: 20,
                eligible_vote_weight_total: 20,
                required_participating_voter_count: 2,
                required_participating_vote_weight_total: 2,
                quorum_met: false,
                quorum_percent: 10,
                quorum_required: true,
                participating_voter_percent: 0,
                participating_vote_weight_percent: 0,
              },
              turnout_rows: [],
              exclusion_groups: [],
              election_is_finished: false,
              tally_winners: [],
              empty_seats: 0,
            },
          }),
        );
      }
      return new Response(
        JSON.stringify({
          candidates: {
            items: [],
            pagination: {
              count: 0,
              page: 1,
              num_pages: 1,
              page_numbers: [1],
              show_first: false,
              show_last: false,
              has_previous: false,
              has_next: false,
              previous_page_number: null,
              next_page_number: null,
              start_index: 0,
              end_index: 0,
            },
          },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    mount(ElectionDetailSummaryPage, {
      props: { bootstrap },
    });

    await Promise.resolve();
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(0);

    expect(chartMock).toHaveBeenCalledOnce();
    expect(chartMock).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        data: expect.objectContaining({
          labels: ["2026-04-02", "2026-04-03", "2026-04-04"],
          datasets: [
            expect.objectContaining({
              data: [0, 0, 0],
            }),
          ],
        }),
      }),
    );
  });
});