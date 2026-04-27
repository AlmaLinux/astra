import { afterEach, describe, expect, it, vi } from "vitest";

import { mountElectionDetailPage } from "../../entrypoints/electionDetail";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-election-detail-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountElectionDetailPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts when required election detail bootstrap data exists", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input) => {
        const url = String(input);
        if (url.includes("/detail")) {
          return new Response(
            JSON.stringify({
              election: {
                id: 1,
                name: "Board election",
                description: "",
                url: "",
                status: "open",
                start_datetime: "2026-04-01T10:00:00+00:00",
                end_datetime: "2026-04-10T10:00:00+00:00",
                viewer_timezone: "UTC",
                number_of_seats: 2,
                quorum: 10,
                eligible_group_cn: "board-voters",
                can_vote: true,
                credential_issued_at: null,
                show_turnout_chart: false,
                turnout_stats: {},
                turnout_chart_data: { labels: [], counts: [] },
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
      }),
    );

    const root = buildRoot({
      "data-election-detail-info-api-url": "/api/v1/elections/1/detail",
      "data-election-detail-candidates-api-url": "/api/v1/elections/1/candidates",
      "data-election-detail-user-profile-url-template": "/user/__username__/",
    });

    const app = mountElectionDetailPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-election-detail-vue-root]")).not.toBeNull();
  });
});