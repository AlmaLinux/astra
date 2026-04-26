import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import UserProfileSummary from "../UserProfileSummary.vue";
import type { UserProfileSummaryBootstrap } from "../types";

const bootstrap: UserProfileSummaryBootstrap = {
  fullName: "Alice User",
  username: "alice",
  email: "alice@example.com",
  avatarUrl: "",
  viewerIsMembershipCommittee: true,
  profileCountry: "United States",
  pronouns: "she/her",
  locale: "en_US",
  timezoneName: "UTC",
  currentTimeLabel: "Thursday 12:00:00",
  ircNicks: ["alice"],
  socialProfiles: [
    {
      label: "GitHub",
      title: "GitHub URLs",
      icon: "fab fa-github",
      urls: [{ href: "https://github.com/alice", text: "@alice" }],
    },
  ],
  websiteUrls: [{ href: "https://example.com", text: "https://example.com" }],
  rssUrls: [],
  rhbzEmail: "alice@redhat.com",
  githubUsername: "alice",
  gitlabUsername: "alice-gl",
  gpgKeys: ["ABC123"],
  sshKeys: ["ssh-ed25519 AAAA"],
  isSelf: true,
};

describe("UserProfileSummary", () => {
  it("renders the profile identity and attributes", () => {
    const wrapper = mount(UserProfileSummary, {
      props: { bootstrap, settingsProfileUrl: "/settings/?tab=profile" },
    });

    expect(wrapper.text()).toContain("Alice User");
    expect(wrapper.text()).toContain("alice@example.com");
    expect(wrapper.text()).toContain("United States");
    expect(wrapper.find('a[href="https://github.com/alice"]').exists()).toBe(true);
  });

  it("falls back to the placeholder icon when no avatar URL exists", () => {
    const wrapper = mount(UserProfileSummary, {
      props: { bootstrap, settingsProfileUrl: "/settings/?tab=profile" },
    });

    expect(wrapper.find(".fa-user").exists()).toBe(true);
  });
});