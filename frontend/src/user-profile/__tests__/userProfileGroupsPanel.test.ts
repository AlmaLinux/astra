import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import UserProfileGroupsPanel from "../UserProfileGroupsPanel.vue";
import type { UserProfileGroupsBootstrap } from "../types";

const bootstrap: UserProfileGroupsBootstrap = {
  username: "alice",
  groups: [{ cn: "infra", role: "Member" }, { cn: "sig-core", role: "Sponsor" }],
  agreements: ["Code of Conduct"],
  missingAgreements: [{ cn: "Export Policy", requiredBy: ["infra"], settingsUrl: "/settings/?tab=agreements" }],
  isSelf: true,
};

describe("UserProfileGroupsPanel", () => {
  it("renders groups and agreement statuses", () => {
    const wrapper = mount(UserProfileGroupsPanel, {
      props: { bootstrap },
    });

    expect(wrapper.text()).toContain("Group");
    expect(wrapper.text()).toContain("Signed");
    expect(wrapper.text()).toContain("Required");
    expect(wrapper.text()).toContain("infra");
    expect(wrapper.text()).toContain("sig-core");
    expect(wrapper.find('a[href="/group/infra/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/settings/?tab=agreements"]').exists()).toBe(true);
  });

  it("shows empty state when no groups or agreements exist", () => {
    const wrapper = mount(UserProfileGroupsPanel, {
      props: {
        bootstrap: {
          username: "alice",
          groups: [],
          agreements: [],
          missingAgreements: [],
          isSelf: false,
        } satisfies UserProfileGroupsBootstrap,
      },
    });

    expect(wrapper.text()).toContain("alice has no group memberships");
  });
});
