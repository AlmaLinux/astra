import { afterEach, describe, expect, it, vi } from "vitest";

import { mountElectionVotePage } from "../../entrypoints/electionVote";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-election-vote-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountElectionVotePage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts when required vote bootstrap data exists", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            election: {
              id: 1,
              name: "Board election",
              start_datetime: "2026-04-01T10:00:00+00:00",
              end_datetime: "2026-04-10T10:00:00+00:00",
              detail_url: "/elections/1/",
              submit_url: "/api/v1/elections/1/vote/submit",
              verify_url: "/api/v1/elections/ballot/verify",
              can_submit_vote: true,
              voter_votes: 1,
            },
            vote_weight_breakdown: [],
            candidates: [],
          }),
        ),
      ),
    );

    const root = buildRoot({
      "data-election-vote-api-url": "/api/v1/elections/1/vote",
    });

    const app = mountElectionVotePage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-election-vote-vue-root]")).not.toBeNull();
  });

  it("does not mount when required vote bootstrap data is missing", () => {
    const root = buildRoot({});

    const app = mountElectionVotePage(root);

    expect(app).toBeNull();
    expect(root.innerHTML).toBe("");
  });
});