import { afterEach, describe, expect, it, vi } from "vitest";

import { mountBallotVerifyPage } from "../../entrypoints/ballotVerify";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-ballot-verify-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountBallotVerifyPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts when required ballot verify bootstrap data exists", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            receipt: "",
            has_query: false,
            is_valid_receipt: false,
            found: false,
            election: null,
            election_status: "",
            submitted_date: "",
            is_superseded: false,
            is_final_ballot: false,
            public_ballots_url: "",
            audit_log_url: "",
            rate_limited: false,
            verification_snippet: "",
          }),
        ),
      ),
    );

    const root = buildRoot({
      "data-ballot-verify-api-url": "/api/v1/elections/ballot/verify",
      "data-ballot-verify-hash-script-url": "/static/verify-ballot-hash.py",
      "data-ballot-verify-chain-script-url": "/static/verify-ballot-chain.py",
      "data-ballot-verify-audit-script-url": "/static/verify-audit-log.py",
      "data-ballot-verify-election-detail-url-template": "/elections/__election_id__/",
      "data-ballot-verify-audit-log-url-template": "/elections/__election_id__/audit/",
    });

    const app = mountBallotVerifyPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-ballot-verify-vue-root]")).not.toBeNull();
  });

  it("does not mount when required ballot verify bootstrap data is missing", () => {
    const root = buildRoot({});

    const app = mountBallotVerifyPage(root);

    expect(app).toBeNull();
    expect(root.innerHTML).toBe("");
  });
});