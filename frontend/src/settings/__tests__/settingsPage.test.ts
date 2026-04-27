import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import SettingsPage from "../SettingsPage.vue";
import type { SettingsBootstrap } from "../types";

const bootstrap: SettingsBootstrap = {
  apiUrl: "/api/v1/settings/detail?tab=emails",
  submitUrl: "/settings/",
  csrfToken: "csrf-token",
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
  initialPayload: {
    activeTab: "emails",
    tabs: ["profile", "emails", "keys", "security", "privacy", "membership"],
    profile: { form: { isBound: false, nonFieldErrors: [], fields: [] }, avatarUrl: "https://example.com/avatar.png", avatarProvider: "Libravatar", avatarIsLocal: false, avatarManageUrl: "", highlight: "", chatDefaults: { mattermostServer: "chat.almalinux.org", mattermostTeam: "almalinux", ircServer: "irc.libera.chat", matrixServer: "matrix.org" }, localeOptions: [], timezoneOptions: [] },
    emails: { form: { isBound: false, nonFieldErrors: [], fields: [{ name: "mail", id: "id_mail", widget: "email", value: "alice@example.org", required: true, disabled: false, errors: [], attrs: { class: "form-control" } }] }, emailIsBlacklisted: true },
    keys: { form: { isBound: false, nonFieldErrors: [], fields: [] } },
    security: { usingOtp: false, password: { form: { isBound: false, nonFieldErrors: [], fields: [] } }, otpAdd: { form: { isBound: false, nonFieldErrors: [], fields: [] } }, otpConfirm: { form: { isBound: false, nonFieldErrors: [], fields: [] }, otpUri: null, otpQrPngB64: null }, otpTokens: [] },
    privacy: { form: { isBound: false, nonFieldErrors: [], fields: [] }, accountDeletionForm: null, activeDeletionRequest: null, privacyWarnings: [] },
    membership: { activeMemberships: [], history: [] },
  },
};

describe("SettingsPage", () => {
  it("renders the shell tabs and existing emails warning from the thin-shell payload", () => {
    const wrapper = mount(SettingsPage, {
      props: { bootstrap },
      attachTo: document.body,
    });

    expect(wrapper.text()).toContain("Settings for");
    expect(wrapper.text()).toContain("Email delivery problem");
    expect(wrapper.find('form[action="/settings/"]').exists()).toBe(true);
    expect(wrapper.find('[data-settings-tab="emails"]').exists()).toBe(true);
  });

  it("renders inline field errors for bound settings forms", () => {
    const wrapper = mount(SettingsPage, {
      props: {
        bootstrap: {
          ...bootstrap,
          initialPayload: {
            ...bootstrap.initialPayload,
            security: {
              usingOtp: false,
              password: {
                form: {
                  isBound: true,
                  nonFieldErrors: [],
                  fields: [
                    { name: "current_password", id: "id_current_password", widget: "password", value: "", required: true, disabled: false, errors: ["Current password is required."], attrs: { class: "form-control" } },
                    { name: "new_password", id: "id_new_password", widget: "password", value: "", required: true, disabled: false, errors: [], attrs: { class: "form-control" } },
                    { name: "confirm_new_password", id: "id_confirm_new_password", widget: "password", value: "", required: true, disabled: false, errors: [], attrs: { class: "form-control" } },
                  ],
                },
              },
              otpAdd: { form: { isBound: false, nonFieldErrors: [], fields: [] } },
              otpConfirm: { form: { isBound: false, nonFieldErrors: [], fields: [] }, otpUri: null, otpQrPngB64: null },
              otpTokens: [],
            },
            privacy: {
              form: { isBound: false, nonFieldErrors: [], fields: [] },
              activeDeletionRequest: null,
              privacyWarnings: [],
              accountDeletionForm: {
                isBound: true,
                nonFieldErrors: [],
                fields: [
                  { name: "reason_category", id: "id_reason_category", widget: "select", value: "", required: true, disabled: false, errors: ["This field is required."], attrs: { class: "form-control" }, options: [{ value: "", label: "---------" }, { value: "privacy", label: "Privacy" }] },
                  { name: "reason_text", id: "id_reason_text", widget: "textarea", value: "", required: false, disabled: false, errors: [], attrs: { class: "form-control", rows: "4" } },
                  { name: "acknowledge_retained_data", id: "id_acknowledge_retained_data", widget: "checkbox", value: "", required: true, disabled: false, errors: [], attrs: { class: "custom-control-input" }, checked: false },
                  { name: "current_password", id: "id_current_password_delete", widget: "password", value: "", required: true, disabled: false, errors: ["Current password is required."], attrs: { class: "form-control" } },
                ],
              },
            },
            membership: {
              activeMemberships: [
                {
                  membershipTypeCode: "packager",
                  membershipTypeName: "Packager",
                  createdAt: "2026-04-01T00:00:00Z",
                  expiresAt: null,
                  terminationForm: {
                    isBound: true,
                    nonFieldErrors: [],
                    fields: [
                      { name: "reason_category", id: "id_membership_packager_reason_category", widget: "select", value: "", required: true, disabled: false, errors: [], attrs: { class: "form-control" }, options: [{ value: "", label: "---------" }, { value: "other", label: "Other" }] },
                      { name: "reason_text", id: "id_membership_packager_reason_text", widget: "textarea", value: "", required: false, disabled: false, errors: [], attrs: { class: "form-control", rows: "4" } },
                      { name: "current_password", id: "id_membership_packager_current_password", widget: "password", value: "", required: true, disabled: false, errors: ["Unable to verify your current password."], attrs: { class: "form-control" } },
                    ],
                  },
                },
              ],
              history: [],
            },
            emails: {
              form: {
                isBound: true,
                nonFieldErrors: [],
                fields: [
                  { name: "mail", id: "id_mail", widget: "email", value: "not-an-email", required: true, disabled: false, errors: ["Enter a valid email address."], attrs: { class: "form-control" } },
                  { name: "fasRHBZEmail", id: "id_fasRHBZEmail", widget: "email", value: "", required: false, disabled: false, errors: [], attrs: { class: "form-control" } },
                ],
              },
              emailIsBlacklisted: false,
            },
          },
        },
      },
      attachTo: document.body,
    });

    expect(wrapper.text()).toContain("Enter a valid email address.");
    expect(wrapper.text()).toContain("Current password is required.");
    expect(wrapper.text()).toContain("This field is required.");
    expect(wrapper.text()).toContain("Unable to verify your current password.");
    expect(wrapper.find('#id_mail.is-invalid').exists()).toBe(true);
    expect(wrapper.find('#id_current_password.is-invalid').exists()).toBe(true);
    expect(wrapper.find('#id_reason_category.is-invalid').exists()).toBe(true);
    expect(wrapper.find('#id_membership_packager_current_password.is-invalid').exists()).toBe(true);
  });

  it("restores inline OTP token rename controls using the dedicated Django POST route", async () => {
    const wrapper = mount(SettingsPage, {
      props: {
        bootstrap: {
          ...bootstrap,
          initialPayload: {
            ...bootstrap.initialPayload,
            activeTab: "security",
            security: {
              usingOtp: true,
              password: { form: { isBound: false, nonFieldErrors: [], fields: [] } },
              otpAdd: { form: { isBound: false, nonFieldErrors: [], fields: [] } },
              otpConfirm: { form: { isBound: false, nonFieldErrors: [], fields: [] }, otpUri: null, otpQrPngB64: null },
              otpTokens: [
                { description: "Laptop", uniqueId: "token-1", disabled: false },
              ],
            },
          },
        },
      },
      attachTo: document.body,
    });

    expect(wrapper.find('button[title="Rename this token"]').exists()).toBe(true);

    await wrapper.get('button[title="Rename this token"]').trigger("click");

    const renameForm = wrapper.get('form[action="/settings/security/otp/rename/"]');
    expect(renameForm.find('input[name="token"]').element.getAttribute("value")).toBe("token-1");
    expect(renameForm.find('input[name="description"]').element.getAttribute("value")).toBe("Laptop");
    expect(renameForm.text()).toContain("Rename");
  });

  it("keeps the profile privacy checkbox on the legacy form-check contract", async () => {
    const wrapper = mount(SettingsPage, {
      props: {
        bootstrap: {
          ...bootstrap,
          initialPayload: {
            ...bootstrap.initialPayload,
            activeTab: "privacy",
            privacy: {
              form: {
                isBound: false,
                nonFieldErrors: [],
                fields: [
                  {
                    name: "fasIsPrivate",
                    id: "id_fasIsPrivate",
                    widget: "checkbox",
                    value: "",
                    required: false,
                    disabled: false,
                    errors: [],
                    attrs: { class: "form-check-input" },
                    checked: false,
                  },
                ],
              },
              accountDeletionForm: null,
              activeDeletionRequest: null,
              privacyWarnings: [],
            },
          },
        },
      },
      attachTo: document.body,
    });

    const checkbox = wrapper.get<HTMLInputElement>("#id_fasIsPrivate");
    const checkboxWrapper = checkbox.element.closest("div");
    const checkboxLabel = wrapper.get('label[for="id_fasIsPrivate"]');
    const form = checkbox.element.form;

    expect(checkboxWrapper?.classList.contains("form-check")).toBe(true);
    expect(checkbox.classes()).toContain("form-check-input");
    expect(checkboxLabel.classes()).toContain("form-check-label");
    expect(checkboxLabel.attributes("for")).toBe("id_fasIsPrivate");
    expect(checkbox.element.checked).toBe(false);
    expect(form).not.toBeNull();
    expect(new FormData(form as HTMLFormElement).has("fasIsPrivate")).toBe(false);

    await checkbox.trigger("click");

    expect(checkbox.element.checked).toBe(true);
    expect(new FormData(form as HTMLFormElement).has("fasIsPrivate")).toBe(true);
  });

  it("keeps the deletion acknowledgement checkbox on the legacy form-check contract", async () => {
    const wrapper = mount(SettingsPage, {
      props: {
        bootstrap: {
          ...bootstrap,
          initialPayload: {
            ...bootstrap.initialPayload,
            activeTab: "privacy",
            privacy: {
              form: { isBound: false, nonFieldErrors: [], fields: [] },
              activeDeletionRequest: null,
              privacyWarnings: [],
              accountDeletionForm: {
                isBound: false,
                nonFieldErrors: [],
                fields: [
                  { name: "reason_category", id: "id_reason_category", widget: "select", value: "privacy", required: true, disabled: false, errors: [], attrs: { class: "form-control" }, options: [{ value: "", label: "---------" }, { value: "privacy", label: "Privacy" }] },
                  { name: "reason_text", id: "id_reason_text", widget: "textarea", value: "", required: false, disabled: false, errors: [], attrs: { class: "form-control", rows: "4" } },
                  { name: "acknowledge_retained_data", id: "id_acknowledge_retained_data", widget: "checkbox", value: "", required: true, disabled: false, errors: [], attrs: { class: "custom-control-input" }, checked: false },
                  { name: "current_password", id: "id_current_password_delete", widget: "password", value: "", required: true, disabled: false, errors: [], attrs: { class: "form-control" } },
                ],
              },
            },
          },
        },
      },
      attachTo: document.body,
    });

    const checkbox = wrapper.get<HTMLInputElement>("#id_acknowledge_retained_data");
    const checkboxWrapper = checkbox.element.closest("div");
    const checkboxLabel = wrapper.get('label[for="id_acknowledge_retained_data"]');
    const requiredIndicator = wrapper.find('[data-required-indicator-for="id_acknowledge_retained_data"]');
    const requiredIndicatorText = wrapper.find('[data-required-indicator-text-for="id_acknowledge_retained_data"]');
    const form = checkbox.element.form;

    expect(checkboxWrapper?.classList.contains("form-check")).toBe(true);
    expect(checkbox.classes()).toContain("form-check-input");
    expect(checkbox.classes()).not.toContain("custom-control-input");
    expect(checkboxLabel.classes()).toContain("form-check-label");
    expect(checkboxLabel.attributes("for")).toBe("id_acknowledge_retained_data");
    expect(requiredIndicator.exists()).toBe(true);
    expect(requiredIndicator.text()).toBe("*");
    expect(requiredIndicator.attributes("aria-hidden")).toBe("true");
    expect(requiredIndicator.classes()).toContain("form-required-indicator");
    expect(requiredIndicatorText.exists()).toBe(true);
    expect(requiredIndicatorText.text()).toContain("Required");
    expect(requiredIndicatorText.classes()).toContain("form-required-indicator-text");
    expect(requiredIndicatorText.classes()).toContain("sr-only");
    expect(checkbox.element.checked).toBe(false);
    expect(form).not.toBeNull();
    expect(new FormData(form as HTMLFormElement).has("acknowledge_retained_data")).toBe(false);

    await checkbox.trigger("click");

    expect(checkbox.element.checked).toBe(true);
    expect(new FormData(form as HTMLFormElement).has("acknowledge_retained_data")).toBe(true);
  });
});