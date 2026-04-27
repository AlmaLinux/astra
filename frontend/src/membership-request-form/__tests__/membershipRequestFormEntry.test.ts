import { afterEach, describe, expect, it, vi } from "vitest";

import { mountMembershipRequestFormPage } from "../../entrypoints/membershipRequestForm";

function buildRoot(attributes: Record<string, string>, initialPayload?: object): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-membership-request-form-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  if (initialPayload) {
    const script = document.createElement("script");
    script.type = "application/json";
    script.setAttribute("data-membership-request-form-initial-payload", "");
    script.textContent = JSON.stringify(initialPayload);
    root.appendChild(script);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountMembershipRequestFormPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts when the form shell API bootstrap exists", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({
        organization: null,
        no_types_available: false,
        prefill_type_unavailable_name: null,
        form: {
          is_bound: false,
          non_field_errors: [],
          fields: [],
        },
      }))),
    );

    const root = buildRoot({
      "data-membership-request-form-api-url": "/api/v1/membership/request/detail",
      "data-membership-request-form-cancel-url": "/user/alice/",
      "data-membership-request-form-submit-url": "/membership/request/",
      "data-membership-request-form-page-title": "Request Membership",
      "data-membership-request-form-privacy-policy-url": "/privacy-policy/",
    });

    const app = mountMembershipRequestFormPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-membership-request-form-vue-root]")).not.toBeNull();
  });

  it("mounts from initial payload without fetching", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const root = buildRoot(
      {
        "data-membership-request-form-cancel-url": "/user/alice/",
        "data-membership-request-form-submit-url": "/membership/request/",
        "data-membership-request-form-page-title": "Request Membership",
        "data-membership-request-form-privacy-policy-url": "/privacy-policy/",
      },
      {
        organization: null,
        no_types_available: true,
        prefill_type_unavailable_name: null,
        form: {
          is_bound: true,
          non_field_errors: [],
          fields: [],
        },
      },
    );

    const app = mountMembershipRequestFormPage(root);

    expect(app).not.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(root.querySelector("[data-membership-request-form-vue-root]")).not.toBeNull();
  });
});