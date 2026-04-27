import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import MembershipRequestFormPage from "../MembershipRequestFormPage.vue";
import type { MembershipRequestFormBootstrap, MembershipRequestFormPayload } from "../types";

const bootstrap: MembershipRequestFormBootstrap = {
  apiUrl: "",
  cancelUrl: "/user/alice/",
  submitUrl: "/membership/request/",
  pageTitle: "Request Membership",
  privacyPolicyUrl: "/privacy-policy/",
  initialPayload: {
    organization: null,
    noTypesAvailable: false,
    prefillTypeUnavailableName: null,
    form: {
      isBound: false,
      nonFieldErrors: [],
      fields: [
        {
          name: "membership_type",
          id: "id_membership_type",
          label: "Membership type",
          widget: "select",
          value: "individual",
          required: true,
          disabled: false,
          helpText: "",
          errors: [],
          attrs: { class: "form-control w-100" },
          optionGroups: [
            {
              label: null,
              options: [
                { value: "individual", label: "Individual", selected: true, disabled: false, category: "individual" },
                { value: "mirror", label: "Mirror", selected: false, disabled: false, category: "mirror" },
              ],
            },
          ],
        },
        {
          name: "q_contributions",
          id: "id_q_contributions",
          label: "Please provide a summary of your contributions to the AlmaLinux Community, including links if appropriate.",
          widget: "textarea",
          value: "",
          required: false,
          disabled: false,
          helpText: "",
          errors: [],
          attrs: { class: "form-control w-100", rows: "6", spellcheck: "true" },
        },
        {
          name: "q_domain",
          id: "id_q_domain",
          label: "Domain name of the mirror",
          widget: "text",
          value: "",
          required: false,
          disabled: false,
          helpText: "",
          errors: [],
          attrs: { class: "form-control w-100", inputmode: "url", autocomplete: "url" },
        },
        {
          name: "q_pull_request",
          id: "id_q_pull_request",
          label: "Please provide a link to your pull request on https://github.com/AlmaLinux/mirrors/",
          widget: "text",
          value: "",
          required: false,
          disabled: false,
          helpText: "",
          errors: [],
          attrs: { class: "form-control w-100" },
        },
        {
          name: "q_sponsorship_details",
          id: "id_q_sponsorship_details",
          label: "Please describe your organization's sponsorship goals and planned community participation.",
          widget: "textarea",
          value: "",
          required: false,
          disabled: false,
          helpText: "",
          errors: [],
          attrs: { class: "form-control w-100", rows: "4", spellcheck: "true" },
        },
      ],
    },
  },
};

describe("MembershipRequestFormPage", () => {
  it("renders the self-service create form and switches question groups by membership type", async () => {
    const wrapper = mount(MembershipRequestFormPage, {
      props: { bootstrap },
    });

    expect(wrapper.text()).toContain("Membership is subject to confirmation of eligibility.");
    expect(wrapper.find('form[action="/membership/request/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/user/alice/"]').text()).toContain("Cancel");
    expect(wrapper.find('a[href="/privacy-policy/"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="questions-individual"]').attributes("style") || "").not.toContain("display: none");
    expect(wrapper.find('[data-test="questions-mirror"]').attributes("style") || "").toContain("display: none");

    await wrapper.get("#id_membership_type").setValue("mirror");

    expect(wrapper.find('[data-test="questions-individual"]').attributes("style") || "").toContain("display: none");
    expect(wrapper.find('[data-test="questions-mirror"]').attributes("style") || "").not.toContain("display: none");
    expect(wrapper.get("#id_q_domain").attributes("required")).toBeDefined();
    expect(wrapper.get("#id_q_pull_request").attributes("required")).toBeDefined();
  });

  it("renders the no-types-available state from the raw payload", () => {
    const wrapper = mount(MembershipRequestFormPage, {
      props: {
        bootstrap: {
          ...bootstrap,
          initialPayload: {
            organization: null,
            noTypesAvailable: true,
            prefillTypeUnavailableName: null,
            form: {
              isBound: false,
              nonFieldErrors: [],
              fields: [],
            },
          } satisfies MembershipRequestFormPayload,
        },
      },
    });

    expect(wrapper.text()).toContain("Thank you for your support of AlmaLinux!");
    expect(wrapper.text()).toContain("there are no additional memberships available for you to apply for at this time.");
    expect(wrapper.find('a[href="/user/alice/"]').text()).toContain("Back to your profile");
  });

  it("renders the organization-specific sponsorship prefill from raw payload data", () => {
    const wrapper = mount(MembershipRequestFormPage, {
      props: {
        bootstrap: {
          ...bootstrap,
          cancelUrl: "/organization/7/",
          submitUrl: "/organization/7/membership/request/",
          initialPayload: {
            organization: { id: 7, name: "Acme Org" },
            noTypesAvailable: false,
            prefillTypeUnavailableName: null,
            form: {
              isBound: false,
              nonFieldErrors: [],
              fields: [
                {
                  ...bootstrap.initialPayload!.form.fields[0],
                  value: "silver",
                  optionGroups: [
                    {
                      label: null,
                      options: [
                        { value: "mirror", label: "Mirror", selected: false, disabled: false, category: "mirror" },
                      ],
                    },
                    {
                      label: "Sponsorship",
                      options: [
                        { value: "silver", label: "Silver Sponsor Member", selected: true, disabled: false, category: "sponsorship" },
                      ],
                    },
                  ],
                },
                ...bootstrap.initialPayload!.form.fields.slice(1),
              ],
            },
          },
        },
      },
    });

    expect(wrapper.find('form[action="/organization/7/membership/request/"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="questions-sponsorship"]').attributes("style") || "").not.toContain("display: none");
    expect(wrapper.get("#id_q_sponsorship_details").attributes("required")).toBeDefined();
  });

  it("preserves backend widget attrs on rendered controls", async () => {
    const wrapper = mount(MembershipRequestFormPage, {
      props: { bootstrap },
    });

    expect(wrapper.get("#id_q_contributions").attributes("spellcheck")).toBe("true");

    await wrapper.get("#id_membership_type").setValue("mirror");

    expect(wrapper.get("#id_q_domain").attributes("inputmode")).toBe("url");
    expect(wrapper.get("#id_q_domain").attributes("autocomplete")).toBe("url");
  });
});