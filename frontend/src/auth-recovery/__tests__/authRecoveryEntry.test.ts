import { afterEach, describe, expect, it, vi } from "vitest";

import {
  mountOtpSyncPage,
  mountPasswordExpiredPage,
  mountPasswordResetConfirmPage,
  mountPasswordResetRequestPage,
} from "../../entrypoints/authRecovery";

function buildRoot(attributeName: string, attributes: Record<string, string>, initialPayload?: object): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute(attributeName, "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  if (initialPayload) {
    const script = document.createElement("script");
    script.type = "application/json";
    script.setAttribute("data-auth-recovery-initial-payload", "");
    script.textContent = JSON.stringify(initialPayload);
    root.appendChild(script);
  }
  document.body.appendChild(root);
  return root;
}

describe("auth recovery entrypoints", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts the password reset request shell from embedded initial payload without fetching", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const root = buildRoot(
      "data-auth-recovery-password-reset-root",
      {
        "data-auth-recovery-password-reset-api-url": "/api/v1/password-reset/detail",
        "data-auth-recovery-password-reset-submit-url": "/password-reset/",
        "data-auth-recovery-password-reset-login-url": "/login/",
      },
      {
        form: {
          is_bound: false,
          non_field_errors: [],
          fields: [],
        },
      },
    );

    const app = mountPasswordResetRequestPage(root);

    expect(app).not.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(root.querySelector("[data-auth-recovery-password-reset-vue-root]")).not.toBeNull();
  });

  it("mounts the confirm, expired, and otp sync shells when required bootstrap attrs exist", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("confirm")) {
          return new Response(
            JSON.stringify({
              username: "alice",
              has_otp: false,
              form: {
                is_bound: false,
                non_field_errors: [],
                fields: [
                  { name: "password", id: "id_password", widget: "password", value: "", required: true, disabled: false, errors: [], attrs: {} },
                ],
              },
            }),
          );
        }
        if (url.includes("password-expired")) {
          return new Response(
            JSON.stringify({
              form: {
                is_bound: false,
                non_field_errors: [],
                fields: [
                  { name: "username", id: "id_username", widget: "text", value: "alice", required: true, disabled: false, errors: [], attrs: {} },
                ],
              },
            }),
          );
        }
        return new Response(
          JSON.stringify({
            form: {
              is_bound: false,
              non_field_errors: [],
              fields: [
                { name: "username_or_email", id: "id_username_or_email", widget: "text", value: "", required: true, disabled: false, errors: [], attrs: {} },
              ],
            },
          }),
        );
      }),
    );

    const confirmRoot = buildRoot("data-auth-recovery-password-reset-confirm-root", {
      "data-auth-recovery-password-reset-confirm-api-url": "/api/v1/password-reset/confirm/detail?token=abc",
      "data-auth-recovery-password-reset-confirm-submit-url": "/password-reset/confirm/?token=abc",
      "data-auth-recovery-password-reset-confirm-login-url": "/login/",
      "data-auth-recovery-password-reset-confirm-token": "abc",
    });
    const expiredRoot = buildRoot("data-auth-recovery-password-expired-root", {
      "data-auth-recovery-password-expired-api-url": "/api/v1/password-expired/detail",
      "data-auth-recovery-password-expired-submit-url": "/password-expired/",
      "data-auth-recovery-password-expired-login-url": "/login/",
    });
    const otpRoot = buildRoot("data-auth-recovery-otp-sync-root", {
      "data-auth-recovery-otp-sync-api-url": "/api/v1/otp/sync/detail",
      "data-auth-recovery-otp-sync-submit-url": "/otp/sync/",
      "data-auth-recovery-otp-sync-login-url": "/login/",
    });

    expect(mountPasswordResetConfirmPage(confirmRoot)).not.toBeNull();
    expect(mountPasswordExpiredPage(expiredRoot)).not.toBeNull();
    expect(mountOtpSyncPage(otpRoot)).not.toBeNull();
  });
});