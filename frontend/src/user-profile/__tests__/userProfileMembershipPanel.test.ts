import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import UserProfileMembershipPanel from "../UserProfileMembershipPanel.vue";
import type { UserProfileMembershipSection } from "../types";

const membershipManagement = {
  expiryUrlTemplate: "/membership/manage/__username__/__membership_type_code__/expiry/",
  terminateUrlTemplate: "/membership/manage/__username__/__membership_type_code__/terminate/",
  csrfToken: "csrf-token",
  nextUrl: "/user/alice/",
};

const membershipNotesDisabled = {
  summaryUrl: "/api/v1/membership-notes/aggregate/summary/?target_type=user&target=alice",
  detailUrl: "/api/v1/membership-notes/aggregate/?target_type=user&target=alice",
  addUrl: "/api/v1/membership-notes/aggregate/add/",
  csrfToken: "csrf-token",
  nextUrl: "/user/alice/",
  canView: false,
  canWrite: false,
};

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
        membershipType: { name: "Individual", code: "individual", description: "" },
        createdAt: "2024-01-15T12:00:00Z",
        expiresAt: "2026-04-30T00:00:00Z",
        isExpiringSoon: true,
        canRenew: true,
        canRequestTierChange: true,
        canManage: false,
      },
    ],
    pendingEntries: [],
    ...overrides,
  } as UserProfileMembershipSection;
}

describe("UserProfileMembershipPanel", () => {
  it("uses the shared membership card shell", () => {
    const wrapper = mount(UserProfileMembershipPanel, {
      props: {
        membership: makeMembershipSection(),
        timezoneName: "UTC",
        membershipHistoryUrlTemplate: "/membership/log/__username__/?username=__username__",
        membershipRequestUrl: "/membership/request/",
        membershipRequestDetailUrlTemplate: "/membership/request/__request_id__/",
        membershipManagement,
        membershipNotes: membershipNotesDisabled,
      },
    });

    const sharedCard = wrapper.find("[data-membership-card-root]");
    expect(sharedCard.exists()).toBe(true);
    expect(sharedCard.attributes("data-user-profile-membership-root")).toBe("");
    expect(sharedCard.text()).toContain("Membership");
    expect(sharedCard.text()).toContain("Member since January 2024");
    expect(sharedCard.text()).toContain("Expires Apr 30, 2026");
    expect(wrapper.find(".membership-standard.alx-status-badge--active").exists()).toBe(true);
  });

  it("derives renewal and tier-change links from the shell-owned membership request URL", () => {
    const wrapper = mount(UserProfileMembershipPanel, {
      props: {
        membership: makeMembershipSection(),
        timezoneName: "UTC",
        membershipHistoryUrlTemplate: "/membership/log/__username__/?username=__username__",
        membershipRequestUrl: "/membership/request/",
        membershipRequestDetailUrlTemplate: "/membership/request/__request_id__/",
        membershipManagement,
        membershipNotes: membershipNotesDisabled,
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
              membershipType: { name: "Individual", code: "individual", description: "" },
              createdAt: "2024-01-15T12:00:00Z",
              expiresAt: null,
              isExpiringSoon: false,
              canRenew: false,
              canRequestTierChange: false,
              canManage: false,
            },
          ],
        }),
        timezoneName: "UTC",
        membershipHistoryUrlTemplate: "/membership/log/__username__/?username=__username__",
        membershipRequestUrl: "/membership/request/",
        membershipRequestDetailUrlTemplate: "/membership/request/__request_id__/",
        membershipManagement,
        membershipNotes: membershipNotesDisabled,
      },
    });

    expect(wrapper.text()).not.toContain("Request renewal");
    expect(wrapper.text()).not.toContain("Change tier");
  });

  it("derives the pending badge label and classes from raw status", () => {
    const wrapper = mount(UserProfileMembershipPanel, {
      props: {
        membership: makeMembershipSection({
          entries: [],
          pendingEntries: [
            {
              kind: "pending",
              key: "pending-42",
              membershipType: { name: "Individual", code: "individual", description: "Pending individual membership" },
              requestId: 42,
              status: "on_hold",
              organizationName: "Acme Org",
            },
          ],
        }),
        timezoneName: "UTC",
        membershipHistoryUrlTemplate: "/membership/log/__username__/?username=__username__",
        membershipRequestUrl: "/membership/request/",
        membershipRequestDetailUrlTemplate: "/membership/request/__request_id__/",
        membershipManagement,
        membershipNotes: membershipNotesDisabled,
      },
    });

    expect(wrapper.text()).toContain("Action required");
    expect(wrapper.find(".membership-action-required.alx-status-badge--action").exists()).toBe(true);
  });

  it("derives the Under review badge from pending status", () => {
    const wrapper = mount(UserProfileMembershipPanel, {
      props: {
        membership: makeMembershipSection({
          entries: [],
          pendingEntries: [
            {
              kind: "pending",
              key: "pending-41",
              membershipType: { name: "Individual", code: "individual", description: "Pending individual membership" },
              requestId: 41,
              status: "pending",
              organizationName: "",
            },
          ],
        }),
        timezoneName: "UTC",
        membershipHistoryUrlTemplate: "/membership/log/__username__/?username=__username__",
        membershipRequestUrl: "/membership/request/",
        membershipRequestDetailUrlTemplate: "/membership/request/__request_id__/",
        membershipManagement,
        membershipNotes: membershipNotesDisabled,
      },
    });

    expect(wrapper.text()).toContain("Under review");
    expect(wrapper.find(".membership-under-review.alx-status-badge--review").exists()).toBe(true);
  });

  it("preserves higher-precision urgent expiration text including timezone", () => {
    const wrapper = mount(UserProfileMembershipPanel, {
      props: {
        membership: makeMembershipSection({
          entries: [
            {
              kind: "membership",
              key: "membership-individual",
              requestId: 11,
              membershipType: { name: "Individual", code: "individual", description: "" },
              createdAt: "2024-01-15T12:00:00Z",
              expiresAt: "2026-04-30T00:00:00Z",
              isExpiringSoon: true,
              canRenew: true,
              canRequestTierChange: true,
              canManage: true,
            },
          ],
        }),
        timezoneName: "UTC",
        membershipHistoryUrlTemplate: "/membership/log/__username__/?username=__username__",
        membershipRequestUrl: "/membership/request/",
        membershipRequestDetailUrlTemplate: "/membership/request/__request_id__/",
        membershipManagement,
        membershipNotes: membershipNotesDisabled,
      },
    });

    expect(wrapper.text()).toContain("Expires Apr 30, 2026 00:00 (UTC)");
    expect(wrapper.text()).toContain("Current expiration: Apr 30, 2026 00:00 (UTC)");
  });

  it("builds management and notes wiring from the shell bootstrap instead of the API payload", () => {
    const wrapper = mount(UserProfileMembershipPanel, {
      props: {
        membership: makeMembershipSection({
          entries: [
            {
              kind: "membership",
              key: "membership-individual",
              requestId: 11,
              membershipType: { name: "Individual", code: "individual", description: "" },
              createdAt: "2024-01-15T12:00:00Z",
              expiresAt: "2026-04-30T00:00:00Z",
              isExpiringSoon: true,
              canRenew: true,
              canRequestTierChange: true,
              canManage: true,
            },
          ],
        }) as unknown as UserProfileMembershipSection,
        timezoneName: "UTC",
        membershipHistoryUrlTemplate: "/membership/log/__username__/?username=__username__",
        membershipRequestUrl: "/membership/request/",
        membershipRequestDetailUrlTemplate: "/membership/request/__request_id__/",
        membershipManagement,
        membershipNotes: {
          summaryUrl: "/api/v1/membership-notes/aggregate/summary/?target_type=user&target=alice",
          detailUrl: "/api/v1/membership-notes/aggregate/?target_type=user&target=alice",
          addUrl: "/api/v1/membership-notes/aggregate/add/",
          csrfToken: "csrf-token",
          nextUrl: "/user/alice/",
          canView: true,
          canWrite: true,
        },
      } as never,
      global: {
        stubs: {
          MembershipNotesCard: {
            template: '<div data-test="notes-card"></div>',
          },
        },
      },
    });

    expect(wrapper.text()).toContain("Edit expiration");
    expect(wrapper.find('form[action="/membership/manage/alice/individual/expiry/"]').exists()).toBe(true);
    expect(wrapper.find('form[action="/membership/manage/alice/individual/terminate/"]').exists()).toBe(true);
    expect(wrapper.find('input[name="csrfmiddlewaretoken"]').attributes("value")).toBe("csrf-token");
    expect(wrapper.find('input[name="next"]').attributes("value")).toBe("/user/alice/");
    expect(wrapper.find('[data-test="notes-card"]').exists()).toBe(true);
  });

  it("shows the change-tier action when canRequestTierChange is true", () => {
    const wrapper = mount(UserProfileMembershipPanel, {
      props: {
        membership: makeMembershipSection({
          entries: [
            {
              kind: "membership",
              key: "membership-individual",
              requestId: 11,
              membershipType: { name: "Individual", code: "individual", description: "" },
              createdAt: "2024-01-15T12:00:00Z",
              expiresAt: null,
              isExpiringSoon: false,
              canRenew: false,
              canRequestTierChange: true,
              canManage: false,
            },
          ],
        }),
        timezoneName: "UTC",
        membershipHistoryUrlTemplate: "/membership/log/__username__/?username=__username__",
        membershipRequestUrl: "/membership/request/",
        membershipRequestDetailUrlTemplate: "/membership/request/__request_id__/",
        membershipManagement,
        membershipNotes: membershipNotesDisabled,
      },
    });

    expect(wrapper.find('a[href="/membership/request/?membership_type=individual"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("Change tier");
  });
});