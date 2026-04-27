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
    root.setAttribute("data-user-profile-api-url", "/api/v1/users/alice/profile/detail");
    root.setAttribute("data-user-profile-settings-profile-url", "/settings/?tab=profile");
    root.setAttribute("data-user-profile-settings-country-code-url", "/settings/?tab=profile&highlight=country_code");
    root.setAttribute("data-user-profile-settings-emails-url", "/settings/?tab=emails");
    root.setAttribute("data-user-profile-membership-history-url-template", "/membership/log/__username__/?username=__username__");
    root.setAttribute("data-user-profile-membership-request-url", "/membership/request/");
    root.setAttribute("data-user-profile-membership-request-detail-url-template", "/membership/request/__request_id__/");
    root.setAttribute("data-user-profile-membership-set-expiry-url-template", "/membership/manage/__username__/__membership_type_code__/expiry/");
    root.setAttribute("data-user-profile-membership-terminate-url-template", "/membership/manage/__username__/__membership_type_code__/terminate/");
    root.setAttribute("data-user-profile-csrf-token", "csrf-token");
    root.setAttribute("data-user-profile-next-url", "/user/alice/");
    root.setAttribute("data-user-profile-membership-notes-summary-url", "/api/v1/membership-notes/aggregate/summary/?target_type=user&target=alice");
    root.setAttribute("data-user-profile-membership-notes-detail-url", "/api/v1/membership-notes/aggregate/?target_type=user&target=alice");
    root.setAttribute("data-user-profile-membership-notes-add-url", "/api/v1/membership-notes/aggregate/add/");
    root.setAttribute("data-user-profile-membership-notes-can-view", "false");
    root.setAttribute("data-user-profile-membership-notes-can-write", "false");
    root.setAttribute("data-user-profile-group-detail-url-template", "/group/__group_name__/");
    root.setAttribute("data-user-profile-agreements-url-template", "/settings/?tab=agreements&agreement=__agreement_cn__");
    document.body.appendChild(root);

    const app = mountUserProfilePage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-user-profile-page-vue-root]"))?.not.toBeNull();
  });
});
