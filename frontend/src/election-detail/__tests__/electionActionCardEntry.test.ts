import { afterEach, describe, expect, it } from "vitest";

import { mountElectionActionCard } from "../../entrypoints/electionDetail";

function buildRoot(): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-election-detail-action-root", "");
  root.setAttribute("data-election-detail-info-api-url", "/api/v1/elections/1/info");
  root.setAttribute("data-election-vote-url", "/elections/1/vote/");
  root.setAttribute("data-election-membership-request-url", "/membership/request/");
  root.setAttribute("data-election-audit-log-url", "/elections/1/audit/");
  root.setAttribute("data-election-public-ballots-url", "/elections/1/public/ballots.json");
  root.setAttribute("data-election-public-audit-url", "/elections/1/public/audit.json");
  document.body.appendChild(root);
  return root;
}

describe("mountElectionActionCard", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("mounts when the action-card bootstrap is present", () => {
    const root = buildRoot();

    const app = mountElectionActionCard(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-election-action-card-vue-root]")).not.toBeNull();
  });
});