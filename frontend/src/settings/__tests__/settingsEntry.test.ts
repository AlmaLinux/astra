import { afterEach, describe, expect, it, vi } from "vitest";

import { mountSettingsPage } from "../../entrypoints/settings";

function buildRoot(attributes: Record<string, string>, initialPayload?: object, routeConfig?: object): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-settings-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  if (initialPayload) {
    const script = document.createElement("script");
    script.type = "application/json";
    script.id = "settings-initial-payload";
    script.textContent = JSON.stringify(initialPayload);
    root.appendChild(script);
  }
  if (routeConfig) {
    const script = document.createElement("script");
    script.type = "application/json";
    script.id = "settings-route-config";
    script.textContent = JSON.stringify(routeConfig);
    root.appendChild(script);
  }
  document.body.appendChild(root);
  return root;
}

describe("settings entrypoint", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts the settings shell from embedded initial payload without fetching", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const root = buildRoot(
      {
        "data-settings-api-url": "/api/v1/settings/detail?tab=security",
        "data-settings-submit-url": "/settings/",
        "data-settings-csrf-token": "csrf",
      },
      {
        active_tab: "security",
        tabs: ["profile", "emails", "keys", "security", "privacy", "membership"],
        profile: { form: { is_bound: false, non_field_errors: [], fields: [] } },
        emails: { form: { is_bound: false, non_field_errors: [], fields: [] }, email_is_blacklisted: false },
        keys: { form: { is_bound: false, non_field_errors: [], fields: [] } },
        security: {
          using_otp: false,
          password: { form: { is_bound: false, non_field_errors: [], fields: [] } },
          otp_add: { form: { is_bound: false, non_field_errors: [], fields: [] } },
          otp_confirm: { form: { is_bound: false, non_field_errors: [], fields: [] }, otp_uri: null, otp_qr_png_b64: null },
          otp_tokens: [],
        },
        privacy: { form: { is_bound: false, non_field_errors: [], fields: [] }, active_deletion_request: null, privacy_warnings: [] },
        membership: { active_memberships: [], history: [] },
      },
      {
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
      },
    );

    const app = mountSettingsPage(root);

    expect(app).not.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(root.querySelector("[data-settings-vue-root]")).not.toBeNull();
  });
});