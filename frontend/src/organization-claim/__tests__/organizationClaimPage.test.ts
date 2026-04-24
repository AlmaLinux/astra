import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import OrganizationClaimPage from "../OrganizationClaimPage.vue";

describe("OrganizationClaimPage", () => {
  it("renders the ready claim state", () => {
    const wrapper = mount(OrganizationClaimPage, {
      props: {
        bootstrap: {
          state: "ready",
          membershipCommitteeEmail: "committee@example.com",
          organizationName: "Claimable Org",
          organizationWebsite: "https://example.com",
          organizationContactEmail: "contact@example.com",
          csrfToken: "csrf-token",
          formAction: "/organizations/claim/token/",
        },
      },
    });

    expect(wrapper.text()).toContain("Claimable Org");
    expect(wrapper.text()).toContain("contact@example.com");
    expect(wrapper.find('form[action="/organizations/claim/token/"]').exists()).toBe(true);
  });

  it("renders the invalid claim state", () => {
    const wrapper = mount(OrganizationClaimPage, {
      props: {
        bootstrap: {
          state: "invalid",
          membershipCommitteeEmail: "committee@example.com",
          organizationName: "",
          organizationWebsite: "",
          organizationContactEmail: "",
          csrfToken: "",
          formAction: "",
        },
      },
    });

    expect(wrapper.text()).toContain("invalid or has expired");
  });
});