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
  electionDetailUrlTemplate: "/elections/__election_id__/",
  auditLogUrlTemplate: "/elections/__election_id__/audit/",
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
            election: { id: 1, name: "Board election" },
            election_status: "tallied",
            submitted_date: "2026-04-10",
            is_superseded: false,
            is_final_ballot: true,
            public_ballots_url: "/elections/1/public/ballots.json",
            public_audit_url: "/elections/1/public/audit.json",
            publication_bundle: { published_at: "2026-04-11T10:15:00Z" },
            chain_version: 2,
            config_manifest_sha256: "c".repeat(64),
            genesis_hash: "b".repeat(64),
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

    expect(wrapper.get('button[type="submit"]').attributes("title")).toBe(
      "Check this ballot receipt code in the ballot ledger",
    );
    expect(wrapper.text()).toContain("Board election");
    expect(wrapper.text()).toContain(
      "Enter the 64-character ballot receipt code you received after submitting your ballot. This page confirms whether a ballot with that code is recorded. It does not show your selections, your identity, or exact timestamps.",
    );
    expect(wrapper.text()).toContain("Yes — a ballot with this receipt code is recorded for this election.");
    expect(wrapper.text()).toContain("included in the final tally");
    expect(wrapper.text()).toContain("Election definition digest");
    expect(wrapper.text()).toContain(
      "For strongest chain_version 2 ballot-chain verification, download both public-ballots.json and public-audit.json from the same published pair.",
    );
    expect(wrapper.text()).toContain(
      "verify-audit-log.py checks the audit and attestation record only; it does not prove ballot-ledger integrity by itself.",
    );
    expect(wrapper.text()).toContain("Matched publication pair published at: 2026-04-11T10:15:00Z");
    expect(wrapper.text()).toContain("c".repeat(64));
    expect(wrapper.find('a[href="/elections/1/public/ballots.json"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/elections/1/public/audit.json"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/elections/1/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/elections/1/audit/"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("election_id = 1");
  });

  it("does not render public verification for closed elections before tally", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            receipt: "b".repeat(64),
            has_query: true,
            is_valid_receipt: true,
            found: true,
            election: { id: 1, name: "Board election" },
            election_status: "closed",
            submitted_date: "2026-04-10",
            is_superseded: false,
            is_final_ballot: true,
            public_ballots_url: "/elections/1/public/ballots.json",
            public_audit_url: "/elections/1/public/audit.json",
            publication_bundle: { published_at: "2026-04-11T10:15:00Z" },
            chain_version: 2,
            config_manifest_sha256: "d".repeat(64),
            genesis_hash: "c".repeat(64),
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

    expect(wrapper.text()).toContain(
      "Enter the 64-character ballot receipt code you received after submitting your ballot. This page confirms whether a ballot with that code is recorded. It does not show your selections, your identity, or exact timestamps.",
    );
    expect(wrapper.text()).toContain("recorded and locked");
    expect(wrapper.text()).toContain("Yes — a ballot with this receipt code is recorded for this election.");
    expect(wrapper.text()).not.toContain("Public verification");
    expect(wrapper.find('a[href="/elections/1/public/ballots.json"]').exists()).toBe(false);
    expect(wrapper.find('a[href="/elections/1/public/audit.json"]').exists()).toBe(false);
    expect(wrapper.find('a[href="/elections/1/audit/"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("Election definition digest");
    expect(wrapper.text()).toContain("d".repeat(64));
  });

  it("does not render public verification for open elections", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            receipt: "d".repeat(64),
            has_query: true,
            is_valid_receipt: true,
            found: true,
            election: { id: 1, name: "Board election" },
            election_status: "open",
            submitted_date: "2026-04-10",
            is_superseded: false,
            is_final_ballot: true,
            public_ballots_url: null,
            public_audit_url: null,
            publication_bundle: null,
            rate_limited: false,
            verification_snippet: "election_id = 1",
          }),
        ),
      ),
    );

    const wrapper = mount(BallotVerifyPage, {
      props: { bootstrap },
    });

    await wrapper.get("#id_receipt").setValue("d".repeat(64));
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("This election is still open, so the final tally is not available yet.");
    expect(wrapper.text()).not.toContain("Public verification");
    expect(wrapper.find('a[href="/elections/1/audit/"]').exists()).toBe(false);
  });

  it("renders the tightened missing-receipt warning", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            receipt: "c".repeat(64),
            has_query: true,
            is_valid_receipt: true,
            found: false,
            election: null,
            election_status: null,
            submitted_date: null,
            is_superseded: false,
            is_final_ballot: false,
            public_ballots_url: null,
            rate_limited: false,
            verification_snippet: "",
          }),
        ),
      ),
    );

    const wrapper = mount(BallotVerifyPage, {
      props: { bootstrap },
    });

    await wrapper.get("#id_receipt").setValue("c".repeat(64));
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("No ballot with this receipt code was found.");
    expect(wrapper.text()).not.toContain("No ballot with this ballot receipt code was found.");
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