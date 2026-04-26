import { afterEach, describe, expect, it, vi } from "vitest";

import { mountMembershipRequestDetailPage } from "../../entrypoints/membershipRequestDetail";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-membership-request-detail-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountMembershipRequestDetailPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts into the thin-shell root when the detail API bootstrap is present", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({
        viewer: {
          mode: "committee",
        },
        request: {
          id: 42,
          status: "pending",
          requested_at: "2026-04-26T10:00:00+00:00",
          requested_by: { show: false, username: "", full_name: "", deleted: false },
          requested_for: { show: false, kind: "user", label: "", username: "", organization_id: null, deleted: false },
          membership_type: { name: "Mirror" },
          responses: [],
        },
        committee: {
          reopen: { show: false },
          actions: {
            canRequestInfo: false,
            showOnHoldApprove: false,
          },
        },
      }))),
    );

    const root = buildRoot({
      "data-membership-request-detail-api-url": "/api/v1/membership/requests/42/detail",
      "data-membership-request-detail-csrf-token": "csrf-token",
      "data-membership-request-detail-page-title": "Membership Request #42",
      "data-membership-request-detail-back-link-url": "/membership/requests/",
      "data-membership-request-detail-back-link-label": "Back to requests",
      "data-membership-request-detail-user-profile-url-template": "/user/__username__/",
      "data-membership-request-detail-organization-detail-url-template": "/organization/__organization_id__/",
    });

    const app = mountMembershipRequestDetailPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-membership-request-detail-vue-root]"))?.not.toBeNull();
  });

  it("stays inert when the detail API bootstrap is absent", () => {
    const root = buildRoot({});

    const app = mountMembershipRequestDetailPage(root);

    expect(app).toBeNull();
    expect(root.innerHTML).toBe("");
  });

  it("stays inert when the shell bootstrap lacks the route templates", () => {
    const root = buildRoot({
      "data-membership-request-detail-api-url": "/api/v1/membership/requests/42/detail",
      "data-membership-request-detail-csrf-token": "csrf-token",
      "data-membership-request-detail-page-title": "Membership Request #42",
      "data-membership-request-detail-back-link-url": "/membership/requests/",
      "data-membership-request-detail-back-link-label": "Back to requests",
    });

    const app = mountMembershipRequestDetailPage(root);

    expect(app).toBeNull();
  });
});