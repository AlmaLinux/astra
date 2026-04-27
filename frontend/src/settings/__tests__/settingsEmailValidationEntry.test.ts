import { afterEach, describe, expect, it, vi } from "vitest";

import { mountSettingsEmailValidationPage } from "../../entrypoints/settingsEmailValidation";

function buildRoot(attributes: Record<string, string>, initialPayload?: object): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-settings-email-validation-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  if (initialPayload) {
    const script = document.createElement("script");
    script.type = "application/json";
    script.id = "settings-email-validation-initial-payload";
    script.textContent = JSON.stringify(initialPayload);
    root.appendChild(script);
  }
  document.body.appendChild(root);
  return root;
}

describe("settings email validation entrypoint", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts the email validation shell from embedded initial payload without fetching", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const root = buildRoot(
      {
        "data-settings-email-validation-api-url": "/api/v1/settings/emails/validate/detail?token=abc",
        "data-settings-email-validation-submit-url": "/settings/emails/validate/?token=abc",
        "data-settings-email-validation-cancel-url": "/settings/?tab=emails",
        "data-settings-email-validation-csrf-token": "csrf-token",
        "data-settings-email-validation-username": "alice",
      },
      {
        email: "new@example.org",
        email_type: "primary",
        is_valid: true,
      },
    );

    const routeConfig = document.createElement("script");
    routeConfig.type = "application/json";
    routeConfig.id = "settings-email-validation-route-config";
    routeConfig.textContent = JSON.stringify({
      profile_url: "/settings/?tab=profile",
      emails_url: "/settings/?tab=emails",
      keys_url: "/settings/?tab=keys",
      security_url: "/settings/?tab=security",
      privacy_url: "/settings/?tab=privacy",
      membership_url: "/settings/?tab=membership",
      agreements_url: "/settings/?tab=agreements",
      user_profile_url: "/users/alice/profile/",
      account_deletion_submit_url: "/settings/privacy/delete-request/",
      otp_enable_url: "/settings/security/otp/enable/",
      otp_disable_url: "/settings/security/otp/disable/",
      otp_delete_url: "/settings/security/otp/delete/",
      otp_rename_url: "/settings/security/otp/rename/",
      membership_terminate_url_template: "/settings/membership/__membership_type_code__/terminate/",
      group_detail_url_template: "/group/__group_name__/",
      agreement_detail_url_template: "/settings/?tab=agreements&agreement=__agreement_cn__",
    });
    root.appendChild(routeConfig);

    const tabs = document.createElement("script");
    tabs.type = "application/json";
    tabs.id = "settings-email-validation-tabs";
    tabs.textContent = JSON.stringify(["profile", "emails", "keys", "security", "privacy", "membership"]);
    root.appendChild(tabs);

    const app = mountSettingsEmailValidationPage(root);

    expect(app).not.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(root.querySelector("[data-settings-email-validation-vue-root]")).not.toBeNull();
  });
});