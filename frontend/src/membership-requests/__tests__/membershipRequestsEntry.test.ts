import { afterEach, describe, expect, it, vi } from "vitest";

import { mountMembershipRequestsPage } from "../../entrypoints/membershipRequests";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-membership-requests-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountMembershipRequestsPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts into the server-authored root when required bootstrap data is present", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/pending")) {
          return new Response(JSON.stringify({ draw: 1, recordsTotal: 0, recordsFiltered: 0, data: [] }));
        }
        return new Response(JSON.stringify({ draw: 1, recordsTotal: 0, recordsFiltered: 0, data: [] }));
      }),
    );

    const root = buildRoot({
      "data-membership-requests-pending-api-url": "/api/v1/membership/requests/pending",
      "data-membership-requests-on-hold-api-url": "/api/v1/membership/requests/on-hold",
      "data-membership-requests-clear-filter-url": "/membership/requests/",
      "data-membership-request-id-sentinel": "123456789",
      "data-membership-request-detail-template": "/membership/request/123456789/",
      "data-membership-request-approve-template": "/membership/requests/123456789/approve/",
      "data-membership-request-approve-on-hold-template": "/membership/requests/123456789/approve-on-hold/",
      "data-membership-request-reject-template": "/membership/requests/123456789/reject/",
      "data-membership-request-rfi-template": "/membership/requests/123456789/rfi/",
      "data-membership-request-ignore-template": "/membership/requests/123456789/ignore/",
      "data-membership-request-note-add-template": "/membership/requests/123456789/notes/add/",
      "data-membership-request-note-summary-template": "/api/v1/membership/notes/123456789/summary",
      "data-membership-request-note-detail-template": "/api/v1/membership/notes/123456789",
      "data-membership-user-profile-template": "/user/__username__/",
      "data-membership-organization-detail-template": "/organization/123456789/",
      "data-membership-requests-notes-can-view": "true",
      "data-membership-requests-notes-can-write": "true",
      "data-membership-requests-notes-can-vote": "true",
      "data-membership-requests-can-request-info": "true",
    });

    const app = mountMembershipRequestsPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-membership-requests-vue-root]"))?.not.toBeNull();
  });

  it("leaves the page inert when required bootstrap data is missing", () => {
    const root = buildRoot({
      "data-membership-requests-on-hold-api-url": "/api/v1/membership/requests/on-hold",
    });

    const app = mountMembershipRequestsPage(root);

    expect(app).toBeNull();
    expect(root.innerHTML).toBe("");
  });
});