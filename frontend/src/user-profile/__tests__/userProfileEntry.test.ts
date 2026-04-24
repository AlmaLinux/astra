import { afterEach, describe, expect, it } from "vitest";

import { mountUserProfileController, mountUserProfileGroupsPanel } from "../../entrypoints/userProfile";

describe("mountUserProfileController", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("mounts when the user profile root exists", () => {
    const root = document.createElement("div");
    root.setAttribute("data-user-profile-root", "");
    document.body.appendChild(root);

    const app = mountUserProfileController(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-user-profile-controller-root]"))?.not.toBeNull();
  });

  it("mounts groups panel when groups bootstrap exists", () => {
    const script = document.createElement("script");
    script.type = "application/json";
    script.id = "user-profile-groups-bootstrap";
    script.textContent = JSON.stringify({
      username: "alice",
      groups: [{ cn: "infra", role: "Member" }],
      agreements: ["coc"],
      missingAgreements: [],
      isSelf: true,
    });
    document.body.appendChild(script);

    const root = document.createElement("div");
    root.setAttribute("data-user-profile-groups-root", "");
    root.setAttribute("data-user-profile-bootstrap-id", "user-profile-groups-bootstrap");
    document.body.appendChild(root);

    const app = mountUserProfileGroupsPanel(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-user-profile-groups-root-vue]"))?.not.toBeNull();
  });
});
