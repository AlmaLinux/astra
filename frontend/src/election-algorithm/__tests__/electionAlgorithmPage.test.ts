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
    expect(wrapper.find('a[href="https://example.com/runbook"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/static/verify-ballot-hash.py"]').exists()).toBe(true);
  });
});