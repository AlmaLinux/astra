import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import BallotVerifyPage from "../BallotVerifyPage.vue";
import type { BallotVerifyBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const bootstrap: BallotVerifyBootstrap = {
  apiUrl: "/api/v1/elections/ballot/verify",
  verifyBallotHashUrl: "/static/verify-ballot-hash.py",
  verifyBallotChainUrl: "/static/verify-ballot-chain.py",
  verifyAuditLogUrl: "/static/verify-audit-log.py",
};

describe("BallotVerifyPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.history.replaceState(null, "", "/elections/ballot/verify/");
  });

  it("submits a receipt lookup and renders tallied verification details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            receipt: "a".repeat(64),
            has_query: true,
            is_valid_receipt: true,
            found: true,
            election: { id: 1, name: "Board election", detail_url: "/elections/1/" },
            election_status: "tallied",
            submitted_date: "2026-04-10",
            is_superseded: false,
            is_final_ballot: true,
            public_ballots_url: "/elections/1/public/ballots.json",
            audit_log_url: "/elections/1/audit/",
            rate_limited: false,
            verification_snippet: "election_id = 1",
          }),
        ),
      ),
    );

    const wrapper = mount(BallotVerifyPage, {
      props: { bootstrap },
    });

    await wrapper.get("#id_receipt").setValue("a".repeat(64));
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Board election");
    expect(wrapper.text()).toContain("included in the final tally");
    expect(wrapper.find('a[href="/elections/1/public/ballots.json"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("election_id = 1");
  });

  it("renders public verification links for closed elections", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            receipt: "b".repeat(64),
            has_query: true,
            is_valid_receipt: true,
            found: true,
            election: { id: 1, name: "Board election", detail_url: "/elections/1/" },
            election_status: "closed",
            submitted_date: "2026-04-10",
            is_superseded: false,
            is_final_ballot: true,
            public_ballots_url: "/elections/1/public/ballots.json",
            audit_log_url: "/elections/1/audit/",
            rate_limited: false,
            verification_snippet: "election_id = 1",
          }),
        ),
      ),
    );

    const wrapper = mount(BallotVerifyPage, {
      props: { bootstrap },
    });

    await wrapper.get("#id_receipt").setValue("b".repeat(64));
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("recorded and locked");
    expect(wrapper.text()).toContain("Public verification");
    expect(wrapper.find('a[href="/elections/1/public/ballots.json"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/elections/1/audit/"]').exists()).toBe(true);
  });

  it("removes the popstate listener on unmount", () => {
    const addEventListener = vi.spyOn(window, "addEventListener");
    const removeEventListener = vi.spyOn(window, "removeEventListener");

    const wrapper = mount(BallotVerifyPage, {
      props: { bootstrap },
    });

    const popstateListener = addEventListener.mock.calls.find(([eventName]) => eventName === "popstate")?.[1];
    expect(popstateListener).toEqual(expect.any(Function));

    wrapper.unmount();

    expect(removeEventListener).toHaveBeenCalledWith("popstate", popstateListener);
  });
});