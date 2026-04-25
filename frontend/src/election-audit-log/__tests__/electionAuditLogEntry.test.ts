import { afterEach, describe, expect, it, vi } from "vitest";

import { mountElectionAuditLogPage } from "../../entrypoints/electionAuditLog";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-election-audit-log-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountElectionAuditLogPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts when required election audit bootstrap data exists", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request) => {
        if (String(url).includes("audit-summary")) {
          return new Response(
            JSON.stringify({
              summary: { ballots_cast: 0, votes_cast: 0, quota: null, empty_seats: 0, tally_elected_users: [], sankey_flows: [], sankey_elected_nodes: [], sankey_eliminated_nodes: [] },
            }),
          );
        }
        return new Response(
          JSON.stringify({
            audit_log: { items: [], pagination: { count: 0, page: 1, num_pages: 1, page_numbers: [1], has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 0, end_index: 0 }, jump_links: [] },
          }),
        );
      }),
    );

    const root = buildRoot({
      "data-election-audit-log-api-url": "/api/v1/elections/1/audit-log",
      "data-election-audit-summary-api-url": "/api/v1/elections/1/audit-summary",
      "data-election-audit-detail-url": "/elections/1/",
      "data-election-audit-algorithm-url": "/elections/algorithm/",
      "data-election-audit-public-ballots-url": "/elections/1/public/ballots.json",
      "data-election-audit-public-audit-url": "/elections/1/public/audit.json",
      "data-election-audit-user-profile-url-template": "/user/__username__/",
      "data-election-audit-election-name": "Board election",
      "data-election-audit-election-status": "tallied",
      "data-election-audit-start-datetime": "2026-04-01T10:00:00+00:00",
      "data-election-audit-end-datetime": "2026-04-10T10:00:00+00:00",
      "data-election-audit-number-of-seats": "2",
      "data-election-audit-algorithm-name": "Meek STV",
      "data-election-audit-algorithm-version": "1.0",
    });

    const app = mountElectionAuditLogPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-election-audit-log-vue-root]")).not.toBeNull();
  });
});