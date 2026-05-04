import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import UserProfilePage from "../UserProfilePage.vue";
import type { UserProfileBootstrap, UserProfileResponse } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const bootstrap: UserProfileBootstrap = {
  apiUrl: "/api/v1/users/alice/profile/detail",
  settingsProfileUrl: "/settings/?tab=profile",
  settingsCountryCodeUrl: "/settings/?tab=profile&highlight=country_code",
  settingsEmailsUrl: "/settings/?tab=emails",
  membershipHistoryUrlTemplate: "/membership/log/__username__/?username=__username__",
  membershipRequestUrl: "/membership/request/",
  membershipRequestDetailUrlTemplate: "/membership/request/__request_id__/",
  membershipManagement: {
    expiryUrlTemplate: "/membership/manage/__username__/__membership_type_code__/expiry/",
    terminateUrlTemplate: "/membership/manage/__username__/__membership_type_code__/terminate/",
    csrfToken: "csrf-token",
    nextUrl: "/user/alice/",
  },
  membershipNotes: {
    summaryUrl: "/api/v1/membership-notes/aggregate/summary/?target_type=user&target=alice",
    detailUrl: "/api/v1/membership-notes/aggregate/?target_type=user&target=alice",
    addUrl: "/api/v1/membership-notes/aggregate/add/",
    csrfToken: "csrf-token",
    nextUrl: "/user/alice/",
    canView: false,
    canWrite: false,
    targetType: "user",
    target: "",
  },
  groupDetailUrlTemplate: "/group/__group_name__/",
  agreementsUrlTemplate: "/settings/?tab=agreements&agreement=__agreement_cn__",
};

function makePayload(overrides: Partial<UserProfileResponse> = {}): UserProfileResponse {
  const payload: UserProfileResponse = {
    summary: {
      fullName: "Alice User",
      username: "alice",
      email: "alice@example.test",
      avatarUrl: "",
      viewerIsMembershipCommittee: false,
      countryCode: "",
      pronouns: "she/her",
      locale: "en_US",
      timezoneName: "UTC",
      ircNicks: ["alice"],
      socialProfiles: [],
      websiteUrls: [],
      rssUrls: [],
      rhbzEmail: "",
      githubUsername: "alice",
      gitlabUsername: "",
      gpgKeys: [],
      sshKeys: [],
      isSelf: true,
    },
    groups: {
      username: "alice",
      groups: [{ cn: "infra", role: "member" }],
      agreements: ["Code of Conduct"],
      missingAgreements: [],
      isSelf: true,
    },
    membership: {
      showCard: true,
      username: "alice",
      canViewHistory: true,
      canRequestAny: true,
      isOwner: true,
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
      pendingEntries: [],
    },
    accountSetup: {
      requiredActions: [{ id: "country-code-missing-alert" }],
      requiredIsRfi: false,
      recommendedActions: [],
      recommendedDismissKey: "",
    },
  };

  return {
    ...payload,
    ...overrides,
  };
}

function stubProfileFetch(payload: UserProfileResponse) {
  const fetchMock = vi.fn(async () => {
    return new Response(JSON.stringify(payload));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("UserProfilePage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
    localStorage.clear();
  });

  it("loads the REST payload and renders profile sections", async () => {
    const fetchMock = stubProfileFetch(makePayload());

    const wrapper = mount(UserProfilePage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/users/alice/profile/detail", expect.objectContaining({ credentials: "same-origin" }));
    expect(wrapper.text()).toContain("Alice User");
    expect(wrapper.text()).toContain("alice@example.test");
    expect(wrapper.text()).toContain("Individual");
    expect(wrapper.text()).toContain("infra");
    expect(wrapper.text()).toContain("Set country code");
    expect(wrapper.find('a[href="/settings/?tab=profile"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/membership/log/alice/?username=alice"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/membership/request/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/group/infra/"]').exists()).toBe(true);
  });

  it("hides the recommended alert when it was previously dismissed", async () => {
    const dismissKey = "astra:profile-recommended-dismissed:alice";
    localStorage.setItem(dismissKey, "1");
    stubProfileFetch(
      makePayload({
        accountSetup: {
          requiredActions: [],
          requiredIsRfi: false,
          recommendedActions: [{ id: "membership-request-recommended-alert" }],
          recommendedDismissKey: dismissKey,
        },
      }),
    );

    const wrapper = mount(UserProfilePage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.find("#account-setup-recommended-alert").exists()).toBe(false);
  });

  it("preserves the fixed-format timezone clock from the REST payload timezone", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-30T12:34:56Z"));
    stubProfileFetch(
      makePayload({
        summary: {
          ...makePayload().summary,
          timezoneName: "UTC",
        },
      }),
    );

    const wrapper = mount(UserProfilePage, {
      props: { bootstrap },
    });

    await Promise.resolve();
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(1);

    expect(wrapper.find("#user-time").text()).toBe("Thursday 12:34:56");
  });

  it("hides the live clock when timezoneName is empty", async () => {
    stubProfileFetch(
      makePayload({
        summary: {
          ...makePayload().summary,
          timezoneName: "",
        },
      }),
    );

    const wrapper = mount(UserProfilePage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.find("#user-time").exists()).toBe(false);
  });
});