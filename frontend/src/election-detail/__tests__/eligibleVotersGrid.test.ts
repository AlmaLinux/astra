import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import EligibleVotersGrid from "../EligibleVotersGrid.vue";
import type { EligibleVotersBootstrap } from "../types";

const bootstrap: EligibleVotersBootstrap = {
  eligibleVotersApiUrl: "/api/v1/elections/1/eligible-voters",
  ineligibleVotersApiUrl: "/api/v1/elections/1/ineligible-voters",
  electionStatus: null,
  sendMailCredentialsApiUrl: null,
  credentialEmailTemplateApiUrl: null,
  credentialEmailPreviewUrl: null,
};

describe("EligibleVotersGrid", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads filtered eligible voters only after the eligible voters card is opened", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          eligible_voters: {
            items: [
              {
                username: "alice",
                full_name: "Alice Example",
                avatar_url: "/avatars/alice.png",
              },
            ],
            pagination: {
              count: 1,
              page: 1,
              num_pages: 1,
              page_numbers: [1],
              show_first: false,
              show_last: false,
              has_previous: false,
              has_next: false,
              previous_page_number: null,
              next_page_number: null,
              start_index: 1,
              end_index: 1,
            },
          },
        }),
      }),
    );

    vi.stubGlobal(
      "window",
      Object.assign(window, {
        location: new URL("https://example.test/elections/1/?eligible_q=ali"),
      }),
    );

    const wrapper = mount(EligibleVotersGrid, { props: { bootstrap } });
    expect(fetch).not.toHaveBeenCalled();

    await wrapper.findAll('button[title="Expand or collapse this section"]')[0]!.trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("alice"));

    expect(fetch).toHaveBeenCalledWith("/api/v1/elections/1/eligible-voters?q=ali&page=1", expect.any(Object));
    expect(wrapper.text()).toContain("Alice Example");
  });

  it("hands the pending voter count from the API to the reminder controls", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          eligible_voters: {
            items: [],
            usernames: ["alice", "bob", "carol"],
            pending_voter_count: 2,
            pagination: {
              count: 0,
              page: 1,
              num_pages: 1,
              page_numbers: [1],
              show_first: false,
              show_last: false,
              has_previous: false,
              has_next: false,
              previous_page_number: null,
              next_page_number: null,
              start_index: 0,
              end_index: 0,
            },
          },
        }),
      }),
    );

    const wrapper = mount(EligibleVotersGrid, {
      props: {
        bootstrap: {
          ...bootstrap,
          electionStatus: "open",
          sendMailCredentialsApiUrl: "/api/v1/elections/1/send-mail-credentials",
          credentialEmailTemplateApiUrl: "/api/v1/elections/1/credential-email-template",
          credentialEmailPreviewUrl: "/elections/1/email/render-preview/",
          reminderProgressApiUrl: "/api/v1/elections/1/mail-progress?kind=reminder",
        },
      },
    });

    await wrapper.findAll('button[title="Expand or collapse this section"]')[0]!.trigger("click");

    await vi.waitFor(() => expect(wrapper.get("[data-testid='send-reminder-pending-voters']").text()).toBe("Remind pending voters (2)"));
  });

  it("loads ineligible voters only after the ineligible voters card is opened", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          ineligible_voters: {
            items: [
              {
                username: "bob",
                full_name: "Bob Example",
                avatar_url: "",
              },
            ],
            details_by_username: {
              bob: {
                reason: "too_new",
                term_start_date: "2026-02-10",
                election_start_date: "2026-02-10",
                days_at_start: 0,
                days_short: 30,
              },
            },
            pagination: {
              count: 1,
              page: 1,
              num_pages: 1,
              page_numbers: [1],
              show_first: false,
              show_last: false,
              has_previous: false,
              has_next: false,
              previous_page_number: null,
              next_page_number: null,
              start_index: 1,
              end_index: 1,
            },
          },
        }),
      }),
    );

    vi.stubGlobal(
      "window",
      Object.assign(window, {
        location: new URL("https://example.test/elections/1/?ineligible_q=bo"),
      }),
    );

    const wrapper = mount(EligibleVotersGrid, { props: { bootstrap } });
    expect(fetch).not.toHaveBeenCalled();

    await wrapper.findAll('button[title="Expand or collapse this section"]')[1]!.trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("Bob Example"));

    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/elections/1/ineligible-voters?q=bo&page=1",
      expect.any(Object),
    );
    expect(wrapper.text()).toContain("Bob Example");

    await wrapper.get('a[href="/user/bob/"]').trigger("click");

    expect(wrapper.text()).toContain("Membership or sponsorship is active, but too new at the reference date.");
    expect(wrapper.text()).toContain("30");
  });
});