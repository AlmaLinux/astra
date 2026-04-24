import { afterEach, describe, expect, it } from "vitest";

import { mountOrganizationForm } from "../../entrypoints/organizationForm";

describe("mountOrganizationForm", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("mounts when the organization form root exists", () => {
    const root = document.createElement("div");
    root.setAttribute("data-organization-form-root", "");
    document.body.appendChild(root);

    const app = mountOrganizationForm(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-organization-form-vue-root]"))?.not.toBeNull();
  });
});
