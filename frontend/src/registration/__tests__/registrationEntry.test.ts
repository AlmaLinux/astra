import { afterEach, describe, expect, it, vi } from "vitest";

import { mountRegisterPage, mountRegisterConfirmPage, mountRegisterActivatePage } from "../../entrypoints/registration";

function buildRoot(attributeName: string, attributes: Record<string, string>, initialPayload?: object): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute(attributeName, "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  if (initialPayload) {
    const script = document.createElement("script");
    script.type = "application/json";
    script.setAttribute("data-registration-initial-payload", "");
    script.textContent = JSON.stringify(initialPayload);
    root.appendChild(script);
  }
  document.body.appendChild(root);
  return root;
}

describe("registration entrypoints", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts the register shell from embedded initial payload without fetching", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const root = buildRoot(
      "data-register-root",
      {
        "data-register-api-url": "/api/v1/register/detail",
        "data-register-login-url": "/login/",
        "data-register-register-url": "/register/",
        "data-register-submit-url": "/register/",
      },
      {
        registration_open: true,
        form: { is_bound: false, non_field_errors: [], fields: [] },
      },
    );

    const app = mountRegisterPage(root);

    expect(app).not.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(root.querySelector("[data-register-vue-root]")).not.toBeNull();
  });

  it("mounts the confirm and activate shells when required bootstrap attrs exist", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("confirm")) {
          return new Response(JSON.stringify({
            username: "alice",
            email: "alice@example.com",
            form: { is_bound: false, non_field_errors: [], fields: [{ name: "username", id: "id_username", widget: "hidden", value: "alice", required: true, disabled: false, errors: [], attrs: {} }] },
          }));
        }
        return new Response(JSON.stringify({
          username: "alice",
          form: { is_bound: false, non_field_errors: [], fields: [
            { name: "password", id: "id_password", widget: "password", value: "", required: true, disabled: false, errors: [], attrs: { autocomplete: "new-password" } },
            { name: "password_confirm", id: "id_password_confirm", widget: "password", value: "", required: true, disabled: false, errors: [], attrs: { autocomplete: "new-password" } },
          ] },
        }));
      }),
    );

    const confirmRoot = buildRoot("data-register-confirm-root", {
      "data-register-confirm-api-url": "/api/v1/register/confirm/detail?username=alice",
      "data-register-confirm-submit-url": "/register/confirm/?username=alice",
      "data-register-confirm-login-url": "/login/",
    });
    const activateRoot = buildRoot("data-register-activate-root", {
      "data-register-activate-api-url": "/api/v1/register/activate/detail?token=abc",
      "data-register-activate-submit-url": "/register/activate/?token=abc",
      "data-register-activate-start-over-url": "/register/",
    });

    expect(mountRegisterConfirmPage(confirmRoot)).not.toBeNull();
    expect(mountRegisterActivatePage(activateRoot)).not.toBeNull();
  });
});