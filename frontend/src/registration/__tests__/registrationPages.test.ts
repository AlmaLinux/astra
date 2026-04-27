import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import RegistrationActivatePage from "../RegistrationActivatePage.vue";
import RegistrationConfirmPage from "../RegistrationConfirmPage.vue";
import RegistrationPage from "../RegistrationPage.vue";
import type { RegisterActivateBootstrap, RegisterConfirmBootstrap, RegisterPageBootstrap } from "../types";

describe("registration pages", () => {
  it("renders the register page from a data-only payload", () => {
    const bootstrap: RegisterPageBootstrap = {
      apiUrl: "",
      loginUrl: "/login/",
      registerUrl: "/register/?invite=invite-token",
      submitUrl: "/register/",
      initialPayload: {
        registrationOpen: true,
        form: {
          isBound: false,
          nonFieldErrors: [],
          fields: [
            { name: "username", id: "id_username", widget: "text", value: "", required: true, disabled: false, errors: [], attrs: { autocomplete: "username" } },
            { name: "first_name", id: "id_first_name", widget: "text", value: "", required: true, disabled: false, errors: [], attrs: { autocomplete: "given-name" } },
            { name: "last_name", id: "id_last_name", widget: "text", value: "", required: true, disabled: false, errors: [], attrs: { autocomplete: "family-name" } },
            { name: "email", id: "id_email", widget: "email", value: "", required: true, disabled: false, errors: [], attrs: { autocomplete: "email" } },
            { name: "over_16", id: "id_over_16", widget: "checkbox", value: "on", checked: false, required: true, disabled: false, errors: [], attrs: {} },
            { name: "invitation_token", id: "id_invitation_token", widget: "hidden", value: "invite-token", required: false, disabled: false, errors: [], attrs: {} },
          ],
        },
      },
    };

    const wrapper = mount(RegistrationPage, { props: { bootstrap } });

    expect(wrapper.text()).toContain("Step 1 of 3: Account details");
    expect(wrapper.find('form[action="/register/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/login/"]').text()).toContain("Login");
    expect(wrapper.find('a[href="/register/?invite=invite-token"]').text()).toContain("Register");
    expect(wrapper.get("#id_email").attributes("autocomplete")).toBe("email");
  });

  it("renders the confirm page from a data-only payload", () => {
    const bootstrap: RegisterConfirmBootstrap = {
      apiUrl: "",
      submitUrl: "/register/confirm/?username=alice",
      loginUrl: "/login/",
      initialPayload: {
        username: "alice",
        email: "alice@example.com",
        form: {
          isBound: false,
          nonFieldErrors: [],
          fields: [
            { name: "username", id: "id_username", widget: "hidden", value: "alice", required: true, disabled: false, errors: [], attrs: {} },
          ],
        },
      },
    };

    const wrapper = mount(RegistrationConfirmPage, { props: { bootstrap } });

    expect(wrapper.text()).toContain("Step 2 of 3: Verify your email");
    expect(wrapper.text()).toContain("We created the account for alice.");
    expect(wrapper.find('form[action="/register/confirm/?username=alice"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/login/"]').text()).toContain("Back to login");
  });

  it("renders the activate page from a data-only payload", () => {
    const bootstrap: RegisterActivateBootstrap = {
      apiUrl: "",
      submitUrl: "/register/activate/?token=abc",
      startOverUrl: "/register/",
      initialPayload: {
        username: "alice",
        form: {
          isBound: false,
          nonFieldErrors: [],
          fields: [
            { name: "password", id: "id_password", widget: "password", value: "", required: true, disabled: false, errors: [], attrs: { autocomplete: "new-password" } },
            { name: "password_confirm", id: "id_password_confirm", widget: "password", value: "", required: true, disabled: false, errors: [], attrs: { autocomplete: "new-password" } },
          ],
        },
      },
    };

    const wrapper = mount(RegistrationActivatePage, { props: { bootstrap } });

    expect(wrapper.text()).toContain("Step 3 of 3: Choose a password");
    expect(wrapper.text()).toContain("Hello alice.");
    expect(wrapper.find('form[action="/register/activate/?token=abc"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/register/"]').text()).toContain("Start over");
    expect(wrapper.get("#id_password").attributes("autocomplete")).toBe("new-password");
  });
});