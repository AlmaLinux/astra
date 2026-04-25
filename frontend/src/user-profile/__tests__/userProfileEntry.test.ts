import { afterEach, describe, expect, it, vi } from "vitest";

import { mountUserProfilePage } from "../../entrypoints/userProfile";

describe("mountUserProfilePage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts when the user profile API URL exists", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ summary: null, groups: null, membership: null, accountSetup: { requiredActions: [], recommendedActions: [], recommendedDismissKey: "" } }))),
    );

    const root = document.createElement("div");
    root.setAttribute("data-user-profile-root", "");
    root.setAttribute("data-user-profile-api-url", "/api/v1/users/alice/profile");
    document.body.appendChild(root);

    const app = mountUserProfilePage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-user-profile-page-vue-root]"))?.not.toBeNull();
  });
});
