import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import SettingsEmailValidationPage from "../SettingsEmailValidationPage.vue";
import type { SettingsEmailValidationBootstrap } from "../types";

describe("SettingsEmailValidationPage", () => {
  it("renders the email validation confirmation page from a data-only payload", () => {
    const bootstrap: SettingsEmailValidationBootstrap = {
      apiUrl: "/api/v1/settings/emails/validate/detail?token=abc",
      submitUrl: "/settings/emails/validate/?token=abc",
      cancelUrl: "/settings/?tab=emails",
      csrfToken: "csrf-token",
      username: "alice",
      routeConfig: {
        profileUrl: "/settings/?tab=profile",
        emailsUrl: "/settings/?tab=emails",
        keysUrl: "/settings/?tab=keys",
        securityUrl: "/settings/?tab=security",
        privacyUrl: "/settings/?tab=privacy",
        membershipUrl: "/settings/?tab=membership",
        agreementsUrl: "/settings/?tab=agreements",
        userProfileUrl: "/users/alice/profile/",
        accountDeletionSubmitUrl: "/settings/privacy/delete-request/",
        otpEnableUrl: "/settings/security/otp/enable/",
        otpDisableUrl: "/settings/security/otp/disable/",
        otpDeleteUrl: "/settings/security/otp/delete/",
        otpRenameUrl: "/settings/security/otp/rename/",
        membershipTerminateUrlTemplate: "/settings/membership/__membership_type_code__/terminate/",
        groupDetailUrlTemplate: "/group/__group_name__/",
        agreementDetailUrlTemplate: "/settings/?tab=agreements&agreement=__agreement_cn__",
      },
      visibleTabs: ["profile", "emails", "keys", "security", "privacy", "membership"],
      initialPayload: {
        email: "new@example.org",
        emailType: "primary",
        isValid: true,
      },
    };

    const wrapper = mount(SettingsEmailValidationPage, {
      props: { bootstrap },
    });

    expect(wrapper.text()).toContain("Confirm email address change");
    expect(wrapper.find('a[href="/users/alice/profile/"]').text()).toContain("alice");
    expect(wrapper.text()).toContain("new@example.org");
    expect(wrapper.find('form[action="/settings/emails/validate/?token=abc"]').exists()).toBe(true);
    expect(wrapper.find('.card-footer a[href="/settings/?tab=emails"]').text()).toContain("Cancel");
    expect(wrapper.find('input[name="csrfmiddlewaretoken"]').attributes("value")).toBe("csrf-token");
  });
});