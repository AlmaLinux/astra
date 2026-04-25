import { afterEach, describe, expect, it } from "vitest";

import { mountElectionExtendAction } from "../../entrypoints/electionDetail";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-election-extend-action-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountElectionExtendAction", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("mounts when required extend-action bootstrap data exists", () => {
    const root = buildRoot({
      "data-election-extend-api-url": "/api/v1/elections/1/extend-end",
      "data-election-name": "Board election",
      "data-election-current-end-datetime-value": "2026-04-10T10:00",
      "data-election-current-end-datetime-display": "2026-04-10 10:00 UTC",
    });

    const app = mountElectionExtendAction(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-election-extend-action-vue-root]")).not.toBeNull();
  });
});