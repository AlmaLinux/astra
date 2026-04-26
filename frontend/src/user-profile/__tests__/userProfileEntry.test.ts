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
    root.setAttribute("data-user-profile-settings-profile-url", "/settings/?tab=profile");
    root.setAttribute("data-user-profile-settings-country-code-url", "/settings/?tab=profile&highlight=country_code");
    root.setAttribute("data-user-profile-settings-emails-url", "/settings/?tab=emails");
    root.setAttribute("data-user-profile-membership-history-url-template", "/membership/log/__username__/?username=__username__");
    root.setAttribute("data-user-profile-membership-request-url", "/membership/request/");
    root.setAttribute("data-user-profile-membership-request-detail-url-template", "/membership/request/__request_id__/");
    root.setAttribute("data-user-profile-group-detail-url-template", "/group/__group_name__/");
    root.setAttribute("data-user-profile-agreements-url-template", "/settings/?tab=agreements&agreement=__agreement_cn__");
    document.body.appendChild(root);

    const app = mountUserProfilePage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-user-profile-page-vue-root]"))?.not.toBeNull();
  });
});
