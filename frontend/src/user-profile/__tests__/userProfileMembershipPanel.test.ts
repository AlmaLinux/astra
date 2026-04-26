import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import UserProfileMembershipPanel from "../UserProfileMembershipPanel.vue";
import type { UserProfileMembershipSection } from "../types";

function makeMembershipSection(overrides: Partial<UserProfileMembershipSection> = {}): UserProfileMembershipSection {
  return {
    showCard: true,
    username: "alice",
    canViewHistory: true,
    canRequestAny: true,
    isOwner: true,
    entries: [
      {
        kind: "membership",
        key: "membership-individual",
        requestId: 11,
        membershipType: { name: "Individual", code: "individual", description: "", className: "membership-standard" },
        badge: { label: "Individual", className: "badge" },
        memberSinceLabel: "January 2024",
        expiresLabel: "Apr 30, 2026",
        expiresTone: "danger",
        canRenew: true,
        canRequestTierChange: true,
        management: null,
      },
    ],
    pendingEntries: [],
    notes: null,
    ...overrides,
  };
}

describe("UserProfileMembershipPanel", () => {
  it("uses the shared membership card shell", () => {
    const wrapper = mount(UserProfileMembershipPanel, {
      props: {
        membership: makeMembershipSection(),
        membershipHistoryUrlTemplate: "/membership/log/__username__/?username=__username__",
        membershipRequestUrl: "/membership/request/",
        membershipRequestDetailUrlTemplate: "/membership/request/__request_id__/",
      },
    });

    const sharedCard = wrapper.find("[data-membership-card-root]");
    expect(sharedCard.exists()).toBe(true);
    expect(sharedCard.attributes("data-user-profile-membership-root")).toBe("");
    expect(sharedCard.text()).toContain("Membership");
  });

  it("derives renewal and tier-change links from the shell-owned membership request URL", () => {
    const wrapper = mount(UserProfileMembershipPanel, {
      props: {
        membership: makeMembershipSection(),
        membershipHistoryUrlTemplate: "/membership/log/__username__/?username=__username__",
        membershipRequestUrl: "/membership/request/",
        membershipRequestDetailUrlTemplate: "/membership/request/__request_id__/",
      },
    });

    expect(wrapper.find('a[href="/membership/request/?membership_type=individual"]').exists()).toBe(true);
    expect(wrapper.findAll('a[href="/membership/request/?membership_type=individual"]').length).toBe(2);
  });

  it("hides renewal and tier-change buttons when the payload capabilities are false", () => {
    const wrapper = mount(UserProfileMembershipPanel, {
      props: {
        membership: makeMembershipSection({
          entries: [
            {
              kind: "membership",
              key: "membership-individual",
              requestId: null,
              membershipType: { name: "Individual", code: "individual", description: "", className: "membership-standard" },
              badge: { label: "Individual", className: "badge" },
              memberSinceLabel: "January 2024",
              expiresLabel: "",
              expiresTone: "muted",
              canRenew: false,
              canRequestTierChange: false,
              management: null,
            },
          ],
        }),
        membershipHistoryUrlTemplate: "/membership/log/__username__/?username=__username__",
        membershipRequestUrl: "/membership/request/",
        membershipRequestDetailUrlTemplate: "/membership/request/__request_id__/",
      },
    });

    expect(wrapper.text()).not.toContain("Request renewal");
    expect(wrapper.text()).not.toContain("Change tier");
  });
});