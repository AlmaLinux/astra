import { afterEach, describe, expect, it, vi } from "vitest";

import { mountOrganizationDetailPage } from "../../entrypoints/organizationDetail";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-organization-detail-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountOrganizationDetailPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts when required organization detail bootstrap data exists", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              organization: {
                id: 1,
                name: "Acme Org",
                status: "active",
                website: "",
                logo_url: "",
                memberships: [],
                pending_memberships: [],
                representative: { username: "alice", full_name: "Alice" },
                contact_groups: [],
                address: { street: "", city: "", state: "", postal_code: "", country_code: "" },
                notes: null,
              },
            }),
          ),
      ),
    );

    const root = buildRoot({
      "data-organization-detail-api-url": "/api/v1/organizations/1",
      "data-organization-detail-membership-request-detail-template": "/membership/request/__request_id__/",
      "data-organization-detail-membership-request-url": "/organization/1/membership/request/",
      "data-organization-detail-sponsorship-set-expiry-url-template": "/organization/1/sponsorship/__membership_type_code__/expiry/",
      "data-organization-detail-sponsorship-terminate-url-template": "/organization/1/sponsorship/__membership_type_code__/terminate/",
      "data-organization-detail-csrf-token": "csrf-token",
      "data-organization-detail-next-url": "/organization/1/",
      "data-organization-detail-expiry-min-date": "2026-04-01",
      "data-organization-detail-user-profile-url-template": "/user/__username__/",
      "data-organization-detail-send-mail-url-template": "/email-tools/send-mail/?type=manual&to=__email__",
      "data-organization-detail-membership-notes-summary-url": "/api/v1/membership/notes/aggregate/summary?target_type=org&target=1",
      "data-organization-detail-membership-notes-detail-url": "/api/v1/membership/notes/aggregate?target_type=org&target=1",
      "data-organization-detail-membership-notes-add-url": "/api/v1/membership/notes/aggregate/add",
      "data-organization-detail-membership-notes-csrf-token": "csrf",
      "data-organization-detail-membership-notes-next-url": "/organization/1/",
      "data-organization-detail-membership-notes-can-view": "true",
      "data-organization-detail-membership-notes-can-write": "false",
    });

    const app = mountOrganizationDetailPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-organization-detail-vue-root]"))?.not.toBeNull();
  });
});
