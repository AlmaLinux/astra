import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import UserProfileGroupsPanel from "../UserProfileGroupsPanel.vue";
import type { UserProfileGroupsBootstrap } from "../types";

const bootstrap: UserProfileGroupsBootstrap = {
  username: "alice",
  groups: [{ cn: "infra", role: "member" }, { cn: "sig-core", role: "sponsor" }],
  agreements: ["Code of Conduct"],
  missingAgreements: [{ cn: "Export Policy", requiredBy: ["infra"] }],
  isSelf: true,
};

describe("UserProfileGroupsPanel", () => {
  it("renders groups and agreement statuses", () => {
    const wrapper = mount(UserProfileGroupsPanel, {
      props: {
        bootstrap,
        groupDetailUrlTemplate: "/group/__group_name__/",
        agreementsUrlTemplate: "/settings/?tab=agreements&agreement=__agreement_cn__",
        agreementDetailUrlTemplate: "/agreements/__agreement_cn__/",
      },
    });

    expect(wrapper.text()).toContain("Group");
    expect(wrapper.text()).toContain("Signed");
    expect(wrapper.text()).toContain("Required");
    expect(wrapper.text()).toContain("infra");
    expect(wrapper.text()).toContain("sig-core");
    expect(wrapper.find('a[href="/group/infra/"]').exists()).toBe(true);
    expect(wrapper.findAll("a").some((link) => link.attributes("href") === "/settings/?tab=agreements&agreement=Export%20Policy")).toBe(true);
  });

  it("links signed agreements to their document for any viewer", () => {
    const wrapper = mount(UserProfileGroupsPanel, {
      props: {
        bootstrap: { ...bootstrap, isSelf: false },
        groupDetailUrlTemplate: "/group/__group_name__/",
        agreementsUrlTemplate: "/settings/?tab=agreements&agreement=__agreement_cn__",
        agreementDetailUrlTemplate: "/agreements/__agreement_cn__/",
      },
    });

    const link = wrapper.findAll("a").find((candidate) => candidate.attributes("href") === "/agreements/Code%20of%20Conduct/");
    expect(link?.text()).toBe("Code of Conduct");
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
        groupDetailUrlTemplate: "/group/__group_name__/",
        agreementsUrlTemplate: "/settings/?tab=agreements&agreement=__agreement_cn__",
        agreementDetailUrlTemplate: "/agreements/__agreement_cn__/",
      },
    });

    expect(wrapper.text()).toContain("alice has no group memberships");
  });
});
