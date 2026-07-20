import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import ElectionAlgorithmPage from "../ElectionAlgorithmPage.vue";
import type { ElectionAlgorithmBootstrap } from "../types";

const bootstrap: ElectionAlgorithmBootstrap = {
  runbookUrl: "https://example.com/runbook",
  verifyBallotHashUrl: "/static/verify-ballot-hash.py",
  verifyBallotChainUrl: "/static/verify-ballot-chain.py",
  verifyAuditLogUrl: "/static/verify-audit-log.py",
};

describe("ElectionAlgorithmPage brand copy", () => {
  it("uses brand-compliant punctuation in the verification tools list", () => {
    const wrapper = mount(ElectionAlgorithmPage, {
      props: { bootstrap },
    });

    const copy = wrapper.text();

    expect(copy).not.toContain("—");
    expect(copy).toContain("verify-ballot-hash.py: Verify your ballot hash matches your voting intent");
    expect(copy).toContain("verify-ballot-chain.py: Verify the complete ballot chain is unbroken and includes your ballot");
    expect(copy).toContain("verify-audit-log.py: Verify Rekor transparency log attestations in the public audit log");
  });
});