import { afterEach, describe, expect, it } from "vitest";

import { mountElectionTallyAction } from "../../entrypoints/electionDetail";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-election-tally-action-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountElectionTallyAction", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("mounts when required tally-action bootstrap data exists", () => {
    const root = buildRoot({
      "data-election-tally-api-url": "/api/v1/elections/1/tally",
      "data-election-name": "Board election",
    });

    const app = mountElectionTallyAction(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-election-tally-action-vue-root]")).not.toBeNull();
  });
});
