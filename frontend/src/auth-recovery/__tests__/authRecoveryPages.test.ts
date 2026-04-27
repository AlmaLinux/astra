import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import OtpSyncPage from "../OtpSyncPage.vue";
import PasswordExpiredPage from "../PasswordExpiredPage.vue";
import PasswordResetConfirmPage from "../PasswordResetConfirmPage.vue";
import PasswordResetRequestPage from "../PasswordResetRequestPage.vue";
import type {
  OtpSyncBootstrap,
  PasswordExpiredBootstrap,
  PasswordResetConfirmBootstrap,
  PasswordResetRequestBootstrap,
} from "../types";

describe("auth recovery pages", () => {
  it("renders the password reset request page from a data-only payload", () => {
    const bootstrap: PasswordResetRequestBootstrap = {
      apiUrl: "",
      submitUrl: "/password-reset/",
      loginUrl: "/login/",
      initialPayload: {
        form: {
          isBound: false,
          nonFieldErrors: [],
          fields: [
            {
              name: "username_or_email",
              id: "id_username_or_email",
              widget: "text",
              value: "",
              required: true,
              disabled: false,
              errors: [],
              attrs: { autocomplete: "username" },
            },
          ],
        },
      },
    };

    const wrapper = mount(PasswordResetRequestPage, { props: { bootstrap } });

    expect(wrapper.text()).toContain("Reset password");
    expect(wrapper.find('form[action="/password-reset/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/login/"]').text()).toContain("Back to login");
    expect(wrapper.get("#id_username_or_email").attributes("autocomplete")).toBe("username");
  });

  it("renders the password reset confirm page from a data-only payload", () => {
    const bootstrap: PasswordResetConfirmBootstrap = {
      apiUrl: "",
      submitUrl: "/password-reset/confirm/?token=abc",
      loginUrl: "/login/",
      token: "abc",
      initialPayload: {
        username: "alice",
        hasOtp: false,
        form: {
          isBound: false,
          nonFieldErrors: [],
          fields: [
            { name: "password", id: "id_password", widget: "password", value: "", required: true, disabled: false, errors: [], attrs: { autocomplete: "new-password" } },
            { name: "password_confirm", id: "id_password_confirm", widget: "password", value: "", required: true, disabled: false, errors: [], attrs: { autocomplete: "new-password" } },
            { name: "otp", id: "id_otp", widget: "text", value: "", required: false, disabled: false, errors: [], attrs: { autocomplete: "off" } },
          ],
        },
      },
    };

    const wrapper = mount(PasswordResetConfirmPage, { props: { bootstrap } });

    expect(wrapper.text()).toContain("Choose a new password for alice.");
    expect(wrapper.find('form[action="/password-reset/confirm/?token=abc"]').exists()).toBe(true);
    expect(wrapper.find('input[name="token"]').attributes("value")).toBe("abc");
    expect(wrapper.find('a[href="/login/"]').text()).toContain("Back to login");
    expect(wrapper.get("#id_password").attributes("autocomplete")).toBe("new-password");
  });

  it("renders the password expired page from a data-only payload", () => {
    const bootstrap: PasswordExpiredBootstrap = {
      apiUrl: "",
      submitUrl: "/password-expired/",
      loginUrl: "/login/",
      initialPayload: {
        form: {
          isBound: false,
          nonFieldErrors: [],
          fields: [
            { name: "username", id: "id_username", widget: "text", value: "alice", required: true, disabled: false, errors: [], attrs: {} },
            { name: "current_password", id: "id_current_password", widget: "password", value: "", required: true, disabled: false, errors: [], attrs: {} },
            { name: "otp", id: "id_otp", widget: "text", value: "", required: false, disabled: false, errors: [], attrs: {} },
            { name: "new_password", id: "id_new_password", widget: "password", value: "", required: true, disabled: false, errors: [], attrs: {} },
            { name: "confirm_new_password", id: "id_confirm_new_password", widget: "password", value: "", required: true, disabled: false, errors: [], attrs: {} },
          ],
        },
      },
    };

    const wrapper = mount(PasswordExpiredPage, { props: { bootstrap } });

    expect(wrapper.text()).toContain("You must change your password");
    expect(wrapper.find('form[action="/password-expired/"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("Current password");
    expect(wrapper.find('a[href="/login/"]').text()).toContain("Back to login");
  });

  it("renders the otp sync page from a data-only payload", () => {
    const bootstrap: OtpSyncBootstrap = {
      apiUrl: "",
      submitUrl: "/otp/sync/",
      loginUrl: "/login/",
      initialPayload: {
        form: {
          isBound: false,
          nonFieldErrors: [],
          fields: [
            { name: "username", id: "id_username", widget: "text", value: "", required: true, disabled: false, errors: [], attrs: {} },
            { name: "password", id: "id_password", widget: "password", value: "", required: true, disabled: false, errors: [], attrs: {} },
            { name: "first_code", id: "id_first_code", widget: "text", value: "", required: true, disabled: false, errors: [], attrs: { autocomplete: "off" } },
            { name: "second_code", id: "id_second_code", widget: "text", value: "", required: true, disabled: false, errors: [], attrs: { autocomplete: "off" } },
            { name: "token", id: "id_token", widget: "text", value: "", required: false, disabled: false, errors: [], attrs: {} },
          ],
        },
      },
    };

    const wrapper = mount(OtpSyncPage, { props: { bootstrap } });

    expect(wrapper.text()).toContain("Synchronize OTP Token");
    expect(wrapper.text()).toContain("Enter two consecutive OTP codes");
    expect(wrapper.find('form[action="/otp/sync/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/login/"]').text()).toContain("Back to login");
    expect(wrapper.get("#id_first_code").attributes("autocomplete")).toBe("off");
  });
});