import { afterEach, describe, expect, it } from "vitest";

import { mountOrganizationClaimPage } from "../../entrypoints/organizationClaim";

describe("mountOrganizationClaimPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("mounts when the organization claim root exists", () => {
    const root = document.createElement("div");
    root.setAttribute("data-organization-claim-root", "");
    root.setAttribute("data-claim-state", "invalid");
    document.body.appendChild(root);

    const app = mountOrganizationClaimPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-organization-claim-vue-root]"))?.not.toBeNull();
  });
});
