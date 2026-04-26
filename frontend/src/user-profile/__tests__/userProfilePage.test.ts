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
  apiUrl: "/api/v1/users/alice/profile",
  settingsProfileUrl: "/settings/?tab=profile",
  settingsCountryCodeUrl: "/settings/?tab=profile&highlight=country_code",
  settingsEmailsUrl: "/settings/?tab=emails",
  membershipHistoryUrlTemplate: "/membership/log/__username__/?username=__username__",
  membershipRequestUrl: "/membership/request/",
  membershipRequestDetailUrlTemplate: "/membership/request/__request_id__/",
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
      profileCountry: "",
      pronouns: "she/her",
      locale: "en_US",
      timezoneName: "UTC",
      currentTimeLabel: "Thursday 12:00:00",
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
      groups: [{ cn: "infra", role: "Member" }],
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
          membershipType: { name: "Individual", code: "individual", description: "", className: "membership-standard" },
          badge: { label: "Individual", className: "badge alx-status-badge membership-standard alx-status-badge--active" },
          memberSinceLabel: "January 2024",
          expiresLabel: "",
          expiresTone: "muted",
          canRenew: false,
          canRequestTierChange: false,
          management: null,
        },
      ],
      pendingEntries: [],
      notes: null,
    },
    accountSetup: {
      requiredActions: [{ id: "country-code-missing-alert", label: "Add your country", urlLabel: "Set country code" }],
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

function stubProfileFetch(payload: UserProfileResponse): ReturnType<typeof vi.fn> {
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
    localStorage.clear();
  });

  it("loads the REST payload and renders profile sections", async () => {
    const fetchMock = stubProfileFetch(makePayload());

    const wrapper = mount(UserProfilePage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/users/alice/profile", expect.objectContaining({ credentials: "same-origin" }));
    expect(wrapper.text()).toContain("Alice User");
    expect(wrapper.text()).toContain("alice@example.test");
    expect(wrapper.text()).toContain("Individual");
    expect(wrapper.text()).toContain("infra");
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
          recommendedActions: [{ id: "membership-request-recommended-alert", label: "Request membership", urlLabel: "Request" }],
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

  it("updates the displayed timezone clock from the REST payload", async () => {
    stubProfileFetch(
      makePayload({
        summary: {
          ...makePayload().summary,
          timezoneName: "UTC",
          currentTimeLabel: "initial",
        },
      }),
    );

    const wrapper = mount(UserProfilePage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.find("#user-time").text()).not.toBe("initial");
  });
});