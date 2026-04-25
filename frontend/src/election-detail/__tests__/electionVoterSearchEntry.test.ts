import { afterEach, describe, expect, it } from "vitest";

import { mountElectionVoterSearchForm } from "../../entrypoints/electionDetail";

describe("mountElectionVoterSearchForm", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("mounts when the voter search bootstrap data exists", () => {
    const root = document.createElement("div");
    root.setAttribute("data-election-voter-search-root", "");
    root.setAttribute("data-election-search-field-name", "eligible_q");
    root.setAttribute("data-election-search-value", "ali");
    root.setAttribute("data-election-search-placeholder", "Search users...");
    root.setAttribute("data-election-search-aria-label", "Search users");
    root.setAttribute("data-election-search-submit-title", "Search eligible voters");
    root.setAttribute("data-election-search-width", "220px");
    document.body.appendChild(root);

    const app = mountElectionVoterSearchForm(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-election-voter-search-vue-root]")).not.toBeNull();
  });
});