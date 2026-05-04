import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import UserProfileSummary from "../UserProfileSummary.vue";
import type { UserProfileSummaryBootstrap } from "../types";

const bootstrap = {
  fullName: "Alice User",
  username: "alice",
  email: "alice@example.com",
  avatarUrl: "",
  viewerIsMembershipCommittee: true,
  countryCode: "US",
  pronouns: "she/her",
  locale: "en_US",
  timezoneName: "UTC",
  ircNicks: ["alice"],
  socialProfiles: [
    {
      platform: "x",
      urls: ["https://x.com/alice"],
    },
  ],
  websiteUrls: ["https://example.com", "plain.example.test/path"],
  rssUrls: ["example.com/feed.xml"],
  rhbzEmail: "alice@redhat.com",
  githubUsername: "alice",
  gitlabUsername: "alice-gl",
  gpgKeys: ["ABC123"],
  sshKeys: ["ssh-ed25519 AAAA"],
  isSelf: true,
} as unknown as UserProfileSummaryBootstrap;

describe("UserProfileSummary", () => {
  it("renders the profile identity and attributes", () => {
    const wrapper = mount(UserProfileSummary, {
      props: { bootstrap, currentTimeLabel: "Thursday 12:00:00", settingsProfileUrl: "/settings/?tab=profile" },
    });

    expect(wrapper.text()).toContain("Alice User");
    expect(wrapper.text()).toContain("alice@example.com");
    expect(wrapper.text()).toContain("United States");
    expect(wrapper.find('a[href="https://x.com/alice"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("X (Twitter)");
    expect(wrapper.text()).toContain("@alice");
    expect(wrapper.find('a[href="https://example.com"]').text()).toBe("https://example.com");
    expect(wrapper.find('a[href="https://plain.example.test/path"]').text()).toBe("plain.example.test/path");
    expect(wrapper.find('a[href="https://example.com/feed.xml"]').text()).toBe("example.com/feed.xml");
  });

  it("falls back to the placeholder icon when no avatar URL exists", () => {
    const wrapper = mount(UserProfileSummary, {
      props: { bootstrap, currentTimeLabel: "Thursday 12:00:00", settingsProfileUrl: "/settings/?tab=profile" },
    });

    expect(wrapper.find(".fa-user").exists()).toBe(true);
  });

  it("normalizes scheme-less links and leaves unsafe raw URLs as plain text", () => {
    const wrapper = mount(UserProfileSummary, {
      props: {
        bootstrap: {
          ...bootstrap,
          socialProfiles: [
            {
              platform: "x",
              urls: ["//x.com/bob", "javascript://twitter.com/evil"],
            },
          ],
          websiteUrls: ["plain.example.test/path", "javascript:alert(1)"],
          rssUrls: ["feeds.example.com/rss", "ftp://example.com/feed.xml"],
        },
        currentTimeLabel: "Thursday 12:00:00",
        settingsProfileUrl: "/settings/?tab=profile",
      },
    });

    expect(wrapper.find('a[href="https://x.com/bob"]').text()).toBe("@bob");
    expect(wrapper.text()).toContain("@evil");
    expect(wrapper.find('a[href="javascript://twitter.com/evil"]').exists()).toBe(false);
    expect(wrapper.find('a[href="https://plain.example.test/path"]').text()).toBe("plain.example.test/path");
    expect(wrapper.text()).toContain("javascript:alert(1)");
    expect(wrapper.find('a[href="javascript:alert(1)"]').exists()).toBe(false);
    expect(wrapper.find('a[href="https://feeds.example.com/rss"]').text()).toBe("feeds.example.com/rss");
    expect(wrapper.text()).toContain("ftp://example.com/feed.xml");
    expect(wrapper.find('a[href="ftp://example.com/feed.xml"]').exists()).toBe(false);
  });

  it("renders Not provided for committee viewer when countryCode is empty", () => {
    const wrapper = mount(UserProfileSummary, {
      props: {
        bootstrap: {
          ...bootstrap,
          countryCode: "",
        },
        currentTimeLabel: "Thursday 12:00:00",
        settingsProfileUrl: "/settings/?tab=profile",
      },
    });

    expect(wrapper.text()).toContain("Not provided");
  });

  it("hides the pronouns row when pronouns are empty", () => {
    const wrapper = mount(UserProfileSummary, {
      props: {
        bootstrap: {
          ...bootstrap,
          pronouns: "",
        },
        currentTimeLabel: "Thursday 12:00:00",
        settingsProfileUrl: "/settings/?tab=profile",
      },
    });

    expect(wrapper.text()).not.toContain("Pronouns");
  });
});