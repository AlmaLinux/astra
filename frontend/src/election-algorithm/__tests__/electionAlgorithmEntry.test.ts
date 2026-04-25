import { afterEach, describe, expect, it } from "vitest";

import { mountElectionAlgorithmPage } from "../../entrypoints/electionAlgorithm";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-election-algorithm-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountElectionAlgorithmPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("mounts when required bootstrap data exists", () => {
    const root = buildRoot({
      "data-election-algorithm-runbook-url": "https://example.com/runbook",
      "data-election-algorithm-verify-ballot-hash-url": "/static/verify-ballot-hash.py",
      "data-election-algorithm-verify-ballot-chain-url": "/static/verify-ballot-chain.py",
      "data-election-algorithm-verify-audit-log-url": "/static/verify-audit-log.py",
    });

    const app = mountElectionAlgorithmPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-election-algorithm-vue-root]")).not.toBeNull();
  });

  it("does not mount when required bootstrap data is missing", () => {
    const root = buildRoot({});

    const app = mountElectionAlgorithmPage(root);

    expect(app).toBeNull();
    expect(root.innerHTML).toBe("");
  });
});