import { afterEach, describe, expect, it } from "vitest";

import { mountEligibleVotersGrid } from "../../entrypoints/electionDetail";

describe("mountEligibleVotersGrid", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("mounts when the eligible-voters bootstrap data exists", () => {
    const root = document.createElement("div");
    root.setAttribute("data-election-eligible-voters-root", "");
    root.setAttribute("data-election-eligible-voters-api-url", "/api/v1/elections/1/eligible-voters");
    root.setAttribute("data-election-ineligible-voters-api-url", "/api/v1/elections/1/ineligible-voters");
    document.body.appendChild(root);

    const app = mountEligibleVotersGrid(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-election-eligible-voters-vue-root]")).not.toBeNull();
  });
});