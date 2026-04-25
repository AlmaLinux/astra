import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import ElectionActionCard from "../ElectionActionCard.vue";
import type { ElectionActionCardBootstrap } from "../types";

const bootstrap: ElectionActionCardBootstrap = {
  infoApiUrl: "/api/v1/elections/1/info",
  voteUrl: "/elections/1/vote/",
  membershipRequestUrl: "/membership/request/",
  auditLogUrl: "/elections/1/audit/",
  publicBallotsUrl: "/elections/1/public/ballots.json",
  publicAuditUrl: "/elections/1/public/audit.json",
};

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

describe("ElectionActionCard", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the open voter state with email hint and issued timestamp", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({
        election: {
          id: 1,
          name: "Board election",
          description: "",
          url: "",
          status: "open",
          start_datetime: "2026-04-01T10:00:00+00:00",
          end_datetime: "2026-04-10T10:00:00+00:00",
          number_of_seats: 1,
          quorum: 10,
          eligible_group_cn: "voters",
          can_vote: true,
          viewer_email: "viewer@example.com",
          credential_issued_at: "2026-04-01T12:30:00+00:00",
          eligibility_min_membership_age_days: 30,
          show_turnout_chart: false,
          turnout_stats: {},
          turnout_chart_data: { labels: [], counts: [] },
          exclusion_group_messages: [],
          election_is_finished: false,
          tally_winners: [],
          empty_seats: 0,
        },
      }), { status: 200 })),
    );

    const wrapper = mount(ElectionActionCard, { props: { bootstrap } });
    await flushPromises();

    expect(wrapper.text()).toContain("Vote");
    expect(wrapper.text()).toContain("viewer@example.com");
    expect(wrapper.text()).toContain("Voting credential issued: 2026-04-01 12:30 UTC");
  });

  it("renders the open ineligible state with membership request link", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({
        election: {
          id: 1,
          name: "Board election",
          description: "",
          url: "",
          status: "open",
          start_datetime: "2026-04-01T10:00:00+00:00",
          end_datetime: "2026-04-10T10:00:00+00:00",
          number_of_seats: 1,
          quorum: 10,
          eligible_group_cn: "voters",
          can_vote: false,
          viewer_email: null,
          credential_issued_at: null,
          eligibility_min_membership_age_days: 30,
          show_turnout_chart: false,
          turnout_stats: {},
          turnout_chart_data: { labels: [], counts: [] },
          exclusion_group_messages: [],
          election_is_finished: false,
          tally_winners: [],
          empty_seats: 0,
        },
      }), { status: 200 })),
    );

    const wrapper = mount(ElectionActionCard, { props: { bootstrap } });
    await flushPromises();

    expect(wrapper.text()).toContain("You're not eligible to vote in this election.");
    expect(wrapper.text()).toContain("Request membership");
    expect(wrapper.find('a[href="/membership/request/"]').exists()).toBe(true);
  });

  it("renders the finished state using the exact artifact URLs from the payload", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({
        election: {
          id: 1,
          name: "Board election",
          description: "",
          url: "",
          status: "closed",
          start_datetime: "2026-04-01T10:00:00+00:00",
          end_datetime: "2026-04-10T10:00:00+00:00",
          number_of_seats: 1,
          quorum: 10,
          eligible_group_cn: "voters",
          can_vote: false,
          viewer_email: null,
          credential_issued_at: null,
          eligibility_min_membership_age_days: 30,
          show_turnout_chart: false,
          turnout_stats: {},
          turnout_chart_data: { labels: [], counts: [] },
          exclusion_group_messages: [],
          election_is_finished: true,
          tally_winners: [],
          empty_seats: 0,
        },
      }), { status: 200 })),
    );

    const wrapper = mount(ElectionActionCard, {
      props: {
        bootstrap: {
          ...bootstrap,
          publicBallotsUrl: "https://cdn.example.test/ballots.json",
          publicAuditUrl: "https://cdn.example.test/audit.json",
        },
      },
    });
    await flushPromises();

    expect(wrapper.find('a[href="/elections/1/audit/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="https://cdn.example.test/ballots.json"]').exists()).toBe(true);
    expect(wrapper.find('a[href="https://cdn.example.test/audit.json"]').exists()).toBe(true);
  });
});