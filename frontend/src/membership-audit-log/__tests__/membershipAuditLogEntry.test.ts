import { afterEach, describe, expect, it, vi } from "vitest";

import { mountMembershipAuditLogPage } from "../../entrypoints/membershipAuditLog";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-membership-audit-log-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountMembershipAuditLogPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts when required audit-log bootstrap data exists", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ draw: 1, recordsTotal: 0, recordsFiltered: 0, data: [] }))),
    );

    const root = buildRoot({
      "data-membership-audit-log-api-url": "/api/v1/membership/audit-log",
      "data-membership-audit-log-page-size": "50",
      "data-membership-audit-log-initial-q": "",
      "data-membership-audit-log-initial-username": "",
      "data-membership-audit-log-initial-organization": "",
    });

    const app = mountMembershipAuditLogPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-membership-audit-log-vue-root]"))?.not.toBeNull();
  });

  it("does not mount when required bootstrap data is missing", () => {
    const root = buildRoot({
      "data-membership-audit-log-page-size": "50",
    });

    const app = mountMembershipAuditLogPage(root);

    expect(app).toBeNull();
    expect(root.innerHTML).toBe("");
  });
});
