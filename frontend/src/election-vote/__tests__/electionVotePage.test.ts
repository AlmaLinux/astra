import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import ElectionVotePage from "../ElectionVotePage.vue";
import type { ElectionVoteBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const bootstrap: ElectionVoteBootstrap = {
  apiUrl: "/api/v1/elections/1/vote",
  detailUrlTemplate: "/elections/__election_id__/",
  verifyUrl: "/elections/ballot/verify/",
};

describe("ElectionVotePage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.cookie = "csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
    window.location.hash = "";
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
  });

  it("loads the ballot, submits a ranking, and shows the receipt", async () => {
    document.cookie = "csrftoken=test-token; path=/";

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            election: {
              id: 1,
              name: "Board election",
              start_datetime: "2026-04-01T10:00:00+00:00",
              end_datetime: "2026-04-10T10:00:00+00:00",
              submit_url: "/api/v1/elections/1/vote/submit",
              can_submit_vote: true,
              voter_votes: 2,
            },
            vote_weight_breakdown: [{ votes: 2, label: "Individual", org_name: null }],
            candidates: [{ id: 11, username: "alice", label: "Alice User (alice)" }],
          }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: true,
            election_id: 1,
            email_queued: true,
            ballot_hash: "receipt-123",
            chain_version: 2,
            config_manifest_sha256: "d".repeat(64),
            nonce: "nonce-123",
            previous_chain_hash: "prev-123",
            chain_hash: "chain-123",
          }),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ElectionVotePage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    await wrapper.get('button.btn-outline-primary').trigger("click");
    await wrapper.get('#election-credential').setValue("cred-1");
    await wrapper.get('#election-vote-form').trigger("submit.prevent");

    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain("Your vote was recorded. A ballot receipt email was sent to your email address.");
    expect(wrapper.text()).toContain("Submit replacement ballot");
    expect(wrapper.get('#election-receipt').element.getAttribute("value")).toBe("receipt-123");
    expect(wrapper.get("#election-receipt-copy").attributes("title")).toBe("Copy ballot receipt code to clipboard");
    expect(wrapper.get("#election-receipt-verify").text()).toBe("Verify this ballot receipt code");
    expect(wrapper.text()).toContain("Submission nonce");
    expect(wrapper.text()).toContain("Previous ledger hash");
    expect(wrapper.text()).toContain("Current ledger hash");
    expect(wrapper.text()).toContain("Election definition digest");
    expect((wrapper.get("#election-config-manifest-sha256").element as HTMLInputElement).value).toBe("d".repeat(64));
    expect(wrapper.text()).not.toContain("Submission Nonce");
    expect(wrapper.text()).not.toContain("Previous chain hash");
    expect(wrapper.text()).not.toContain("Chain hash");
    expect(wrapper.text()).toContain("No-JS fallback");
    expect(wrapper.text()).toContain("Save this together with your ballot receipt code.");
  });

  it("preserves the username ranking fallback when submitting through Vue", async () => {
    document.cookie = "csrftoken=test-token; path=/";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            election: {
              id: 1,
              name: "Board election",
              start_datetime: "2026-04-01T10:00:00+00:00",
              end_datetime: "2026-04-10T10:00:00+00:00",
              submit_url: "/api/v1/elections/1/vote/submit",
              can_submit_vote: true,
              voter_votes: 1,
            },
            vote_weight_breakdown: [],
            candidates: [{ id: 11, username: "alice", label: "Alice User (alice)" }],
          }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: true,
            election_id: 1,
            email_queued: true,
            ballot_hash: "receipt-123",
            nonce: "nonce-123",
            previous_chain_hash: "prev-123",
            chain_hash: "chain-123",
          }),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ElectionVotePage, { props: { bootstrap } });
    await flushPromises();
    await flushPromises();

    await wrapper.get("#election-credential").setValue("cred-1");
    await wrapper.get('input[name="ranking_usernames"]').setValue("alice");
    await wrapper.get("#election-vote-form").trigger("submit.prevent");
    await flushPromises();

    const submitOptions = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(JSON.parse(String(submitOptions.body))).toMatchObject({
      credential_public_id: "cred-1",
      ranking: [],
      ranking_usernames: "alice",
    });
    expect(wrapper.text()).toContain("Your vote was recorded.");
  });

  it("shows an explicit ballot receipt email fallback message when email delivery is unavailable", async () => {
    document.cookie = "csrftoken=test-token; path=/";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            election: {
              id: 1,
              name: "Board election",
              start_datetime: "2026-04-01T10:00:00+00:00",
              end_datetime: "2026-04-10T10:00:00+00:00",
              submit_url: "/api/v1/elections/1/vote/submit",
              can_submit_vote: true,
              voter_votes: 1,
            },
            vote_weight_breakdown: [],
            candidates: [{ id: 11, username: "alice", label: "Alice User (alice)" }],
          }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: true,
            election_id: 1,
            email_queued: false,
            ballot_hash: "receipt-123",
            nonce: "nonce-123",
            previous_chain_hash: "prev-123",
            chain_hash: "chain-123",
          }),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ElectionVotePage, { props: { bootstrap } });
    await flushPromises();
    await flushPromises();

    await wrapper.get('button.btn-outline-primary').trigger("click");
    await wrapper.get("#election-credential").setValue("cred-1");
    await wrapper.get("#election-vote-form").trigger("submit.prevent");
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Your vote was recorded. (A ballot receipt email could not be sent.)");
  });

  it("shows the vote breakdown tooltip content on hover", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            election: {
              id: 1,
              name: "Board election",
              start_datetime: "2026-04-01T10:00:00+00:00",
              end_datetime: "2026-04-10T10:00:00+00:00",
              submit_url: "/api/v1/elections/1/vote/submit",
              can_submit_vote: true,
              voter_votes: 2,
            },
            vote_weight_breakdown: [{ votes: 2, label: "Individual", org_name: null }],
            candidates: [],
          }),
        ),
      ),
    );

    const wrapper = mount(ElectionVotePage, { props: { bootstrap } });
    await flushPromises();
    await flushPromises();

    await wrapper.get("#vote-breakdown-tooltip").trigger("mouseenter");

    const tooltip = wrapper.get("#vote-breakdown-tooltip-content");
    expect(tooltip.classes()).toContain("show");
    expect(tooltip.text()).toContain("Individual member");
    expect(tooltip.text()).toContain("Total Votes");
  });

  it("shows only the positive eligibility message when the voter has exactly one vote", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            election: {
              id: 1,
              name: "Board election",
              start_datetime: "2026-04-01T10:00:00+00:00",
              end_datetime: "2026-04-10T10:00:00+00:00",
              submit_url: "/api/v1/elections/1/vote/submit",
              can_submit_vote: true,
              voter_votes: 1,
            },
            vote_weight_breakdown: [],
            candidates: [],
          }),
        ),
      ),
    );

    const wrapper = mount(ElectionVotePage, { props: { bootstrap } });
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("You have 1 vote for this election.");
    expect(wrapper.text()).not.toContain("You do not appear to be eligible for this election.");
    expect(wrapper.text()).not.toContain("These votes are applied as extra weight to your single ranked ballot.");
  });

  it("falls back to selecting the receipt input when clipboard API is unavailable", async () => {
    document.cookie = "csrftoken=test-token; path=/";
    Object.defineProperty(document, "execCommand", { configurable: true, value: vi.fn(() => true) });
    const selectMock = vi.spyOn(HTMLInputElement.prototype, "select").mockImplementation(() => {});
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            election: {
              id: 1,
              name: "Board election",
              start_datetime: "2026-04-01T10:00:00+00:00",
              end_datetime: "2026-04-10T10:00:00+00:00",
              submit_url: "/api/v1/elections/1/vote/submit",
              can_submit_vote: true,
              voter_votes: 1,
            },
            vote_weight_breakdown: [],
            candidates: [{ id: 11, username: "alice", label: "Alice User (alice)" }],
          }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: true,
            election_id: 1,
            email_queued: true,
            ballot_hash: "receipt-123",
            nonce: "nonce-123",
            previous_chain_hash: "prev-123",
            chain_hash: "chain-123",
          }),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ElectionVotePage, { props: { bootstrap }, attachTo: document.body });
    await flushPromises();
    await flushPromises();

    await wrapper.get('button.btn-outline-primary').trigger("click");
    await wrapper.get("#election-credential").setValue("cred-1");
    await wrapper.get("#election-vote-form").trigger("submit.prevent");
    await flushPromises();
    await flushPromises();
    await wrapper.get("#election-receipt-copy").trigger("click");

    expect(selectMock).toHaveBeenCalled();
    expect(document.execCommand).toHaveBeenCalledWith("copy");
    expect(wrapper.text()).toContain("Ballot receipt code copied to clipboard.");
  });

  it("shows the explicit ballot receipt code error when clipboard copy fails", async () => {
    document.cookie = "csrftoken=test-token; path=/";
    Object.defineProperty(document, "execCommand", { configurable: true, value: vi.fn(() => false) });
    const selectMock = vi.spyOn(HTMLInputElement.prototype, "select").mockImplementation(() => {});
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            election: {
              id: 1,
              name: "Board election",
              start_datetime: "2026-04-01T10:00:00+00:00",
              end_datetime: "2026-04-10T10:00:00+00:00",
              submit_url: "/api/v1/elections/1/vote/submit",
              can_submit_vote: true,
              voter_votes: 1,
            },
            vote_weight_breakdown: [],
            candidates: [{ id: 11, username: "alice", label: "Alice User (alice)" }],
          }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: true,
            election_id: 1,
            email_queued: true,
            ballot_hash: "receipt-123",
            nonce: "nonce-123",
            previous_chain_hash: "prev-123",
            chain_hash: "chain-123",
          }),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ElectionVotePage, { props: { bootstrap }, attachTo: document.body });
    await flushPromises();
    await flushPromises();

    await wrapper.get('button.btn-outline-primary').trigger("click");
    await wrapper.get("#election-credential").setValue("cred-1");
    await wrapper.get("#election-vote-form").trigger("submit.prevent");
    await flushPromises();
    await flushPromises();
    await wrapper.get("#election-receipt-copy").trigger("click");

    expect(selectMock).toHaveBeenCalled();
    expect(document.execCommand).toHaveBeenCalledWith("copy");
    expect(wrapper.text()).toContain("Copy failed. Please copy the ballot receipt code manually.");
  });
});