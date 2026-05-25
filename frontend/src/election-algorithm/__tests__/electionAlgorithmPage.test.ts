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

describe("ElectionAlgorithmPage", () => {
  it("renders the published algorithm documentation and verification links", () => {
    const wrapper = mount(ElectionAlgorithmPage, {
      props: { bootstrap },
    });

    expect(wrapper.text()).toContain("Meek STV (High-Precision Variant)");
    expect(wrapper.text()).toContain("80-digit precision");
    expect(wrapper.text()).toContain(
      "For chain_version 2 ballot-chain verification, verify-ballot-chain.py uses public-ballots.json together with the matching public-audit.json publication pair.",
    );
    expect(wrapper.text()).toContain(
      "verify-audit-log.py validates the audit and attestation record only; it does not prove ballot-ledger integrity by itself.",
    );
    expect(wrapper.text()).not.toContain(
      "The export also includes a per-election genesis hash that prevents mixing ballots from different elections.",
    );
    expect(wrapper.find('a[href="https://example.com/runbook"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/static/verify-ballot-hash.py"]').exists()).toBe(true);
  });
});