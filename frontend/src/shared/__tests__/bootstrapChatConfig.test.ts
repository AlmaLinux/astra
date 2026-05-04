import { describe, expect, it } from "vitest";

import { readGroupDetailBootstrap } from "../../group-detail/types";
import { readUserProfileBootstrap } from "../../user-profile/types";

function makeGroupDetailRoot(): HTMLDivElement {
  const root = document.createElement("div");
  root.dataset.groupDetailInfoApiUrl = "/api/v1/groups/infra/info";
  root.dataset.groupDetailLeadersApiUrl = "/api/v1/groups/infra/leaders";
  root.dataset.groupDetailMembersApiUrl = "/api/v1/groups/infra/members";
  root.dataset.groupDetailActionUrl = "/api/v1/groups/infra/action";
  root.dataset.groupDetailChatIrcDefaultServer = "irc.libera.chat";
  root.dataset.groupDetailChatMatrixDefaultServer = "matrix.org";
  root.dataset.groupDetailChatMattermostDefaultServer = "chat.almalinux.org";
  root.dataset.groupDetailChatMattermostDefaultTeam = "almalinux";
  root.dataset.groupDetailCurrentUsername = "alice";
  root.dataset.groupDetailUrlTemplate = "/group/__group_name__/";
  root.dataset.groupDetailEditUrlTemplate = "/group/__group_name__/edit/";
  root.dataset.groupDetailAgreementDetailUrlTemplate = "/settings/?tab=agreements&agreement=__agreement_cn__";
  root.dataset.groupDetailAgreementsListUrl = "/settings/?tab=agreements";
  return root;
}

function makeUserProfileRoot(): HTMLDivElement {
  const root = document.createElement("div");
  root.dataset.userProfileApiUrl = "/api/v1/users/alice/profile/detail";
  root.dataset.userProfileChatIrcDefaultServer = "irc.libera.chat";
  root.dataset.userProfileChatMatrixDefaultServer = "matrix.org";
  root.dataset.userProfileChatMattermostDefaultServer = "chat.almalinux.org";
  root.dataset.userProfileChatMattermostDefaultTeam = "almalinux";
  root.dataset.userProfileSettingsProfileUrl = "/settings/?tab=profile";
  root.dataset.userProfileSettingsCountryCodeUrl = "/settings/?tab=profile&highlight=country_code";
  root.dataset.userProfileSettingsEmailsUrl = "/settings/?tab=emails";
  root.dataset.userProfileMembershipHistoryUrlTemplate = "/membership/log/__username__/?username=__username__";
  root.dataset.userProfileMembershipRequestUrl = "/membership/request/";
  root.dataset.userProfileMembershipRequestDetailUrlTemplate = "/membership/request/__request_id__/";
  root.dataset.userProfileMembershipSetExpiryUrlTemplate = "/membership/manage/__username__/__membership_type_code__/expiry/";
  root.dataset.userProfileMembershipTerminateUrlTemplate = "/membership/manage/__username__/__membership_type_code__/terminate/";
  root.dataset.userProfileCsrfToken = "csrf-token";
  root.dataset.userProfileNextUrl = "/user/alice/";
  root.dataset.userProfileMembershipNotesSummaryUrl = "/api/v1/membership-notes/aggregate/summary/?target_type=user&target=alice";
  root.dataset.userProfileMembershipNotesDetailUrl = "/api/v1/membership-notes/aggregate/?target_type=user&target=alice";
  root.dataset.userProfileMembershipNotesAddUrl = "/api/v1/membership-notes/aggregate/add/";
  root.dataset.userProfileMembershipNotesCanView = "false";
  root.dataset.userProfileMembershipNotesCanWrite = "false";
  root.dataset.userProfileGroupDetailUrlTemplate = "/group/__group_name__/";
  root.dataset.userProfileAgreementsUrlTemplate = "/settings/?tab=agreements&agreement=__agreement_cn__";
  return root;
}

describe("chat bootstrap readers", () => {
  it("keeps group detail bootstrap mountable when matrixToArgs is missing", () => {
    const bootstrap = readGroupDetailBootstrap(makeGroupDetailRoot());

    expect(bootstrap).not.toBeNull();
    expect(bootstrap?.chatConfig.matrixToArgs).toBe("");
  });

  it("keeps user profile bootstrap mountable when matrixToArgs is missing", () => {
    const bootstrap = readUserProfileBootstrap(makeUserProfileRoot());

    expect(bootstrap).not.toBeNull();
    expect(bootstrap?.chatConfig.matrixToArgs).toBe("");
  });
});