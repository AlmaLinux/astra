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
      vi.fn(async () => new Response(JSON.stringify({ organization: { id: 1, name: "Acme Org", status: "active", website: "", detail_url: "/organizations/1/", logo_url: "", memberships: [], representative: { username: "alice", full_name: "Alice" }, contact_groups: [], address: { street: "", city: "", state: "", postal_code: "", country_code: "" } } }))),
    );

    const root = buildRoot({
      "data-organization-detail-api-url": "/api/v1/organizations/1",
    });

    const app = mountOrganizationDetailPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-organization-detail-vue-root]"))?.not.toBeNull();
  });
});
