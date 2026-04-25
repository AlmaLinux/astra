import { afterEach, describe, expect, it } from "vitest";

import { mountElectionConcludeAction } from "../../entrypoints/electionDetail";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-election-conclude-action-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountElectionConcludeAction", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("mounts when required conclude-action bootstrap data exists", () => {
    const root = buildRoot({
      "data-election-conclude-api-url": "/api/v1/elections/1/conclude",
      "data-election-name": "Board election",
      "data-election-conclude-quorum-warning": "",
    });

    const app = mountElectionConcludeAction(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-election-conclude-action-vue-root]")).not.toBeNull();
  });
});