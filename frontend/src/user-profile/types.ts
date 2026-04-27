export interface UserProfileSocialProfile {
  platform: string;
  urls: string[];
}

export interface UserProfileSummaryBootstrap {
  fullName: string;
  username: string;
  email: string;
  avatarUrl: string;
  viewerIsMembershipCommittee: boolean;
  countryCode: string;
  pronouns: string;
  locale: string;
  timezoneName: string;
  ircNicks: string[];
  socialProfiles: UserProfileSocialProfile[];
  websiteUrls: string[];
  rssUrls: string[];
  rhbzEmail: string;
  githubUsername: string;
  gitlabUsername: string;
  gpgKeys: string[];
  sshKeys: string[];
  isSelf: boolean;
}

export interface UserProfileGroupItem {
  cn: string;
  role: "member" | "sponsor";
}

export interface UserProfileMissingAgreementItem {
  cn: string;
  requiredBy: string[];
}

export interface UserProfileGroupsBootstrap {
  username: string;
  groups: UserProfileGroupItem[];
  agreements: string[];
  missingAgreements: UserProfileMissingAgreementItem[];
  isSelf: boolean;
}

export interface UserProfileActionItem {
  id: string;
  requestId?: number;
  agreementCn?: string;
}

export interface UserProfileAccountSetup {
  requiredActions: UserProfileActionItem[];
  requiredIsRfi: boolean;
  recommendedActions: UserProfileActionItem[];
  recommendedDismissKey: string;
}

export interface UserProfileMembershipType {
  name: string;
  code: string;
  description: string;
}

export interface UserProfileMembershipManagementAction {
  expiryUrlTemplate: string;
  terminateUrlTemplate: string;
  csrfToken: string;
  nextUrl: string;
}

export interface UserProfileMembershipEntry {
  kind: "membership";
  key: string;
  requestId: number | null;
  membershipType: UserProfileMembershipType;
  createdAt: string | null;
  expiresAt: string | null;
  isExpiringSoon: boolean;
  canRenew: boolean;
  canRequestTierChange: boolean;
  canManage: boolean;
}

export interface UserProfilePendingMembershipEntry {
  kind: "pending";
  key: string;
  membershipType: UserProfileMembershipType;
  requestId: number;
  status: string;
  organizationName: string;
}

export interface UserProfileMembershipNotes {
  summaryUrl: string;
  detailUrl: string;
  addUrl: string;
  csrfToken: string;
  nextUrl: string;
  canView: boolean;
  canWrite: boolean;
  targetType: string;
  target: string;
}

export interface UserProfileMembershipSection {
  showCard: boolean;
  username: string;
  canViewHistory: boolean;
  canRequestAny: boolean;
  isOwner: boolean;
  entries: UserProfileMembershipEntry[];
  pendingEntries: UserProfilePendingMembershipEntry[];
}

export interface UserProfileResponse {
  summary: UserProfileSummaryBootstrap;
  groups: UserProfileGroupsBootstrap;
  membership: UserProfileMembershipSection;
  accountSetup: UserProfileAccountSetup;
}

export interface UserProfileBootstrap {
  apiUrl: string;
  settingsProfileUrl: string;
  settingsCountryCodeUrl: string;
  settingsEmailsUrl: string;
  membershipHistoryUrlTemplate: string;
  membershipRequestUrl: string;
  membershipRequestDetailUrlTemplate: string;
  membershipManagement: UserProfileMembershipManagementAction;
  membershipNotes: UserProfileMembershipNotes;
  groupDetailUrlTemplate: string;
  agreementsUrlTemplate: string;
}

function readBoolean(value: string): boolean {
  return value === "true";
}

export function readUserProfileBootstrap(root: HTMLElement): UserProfileBootstrap | null {
  const apiUrl = String(root.dataset.userProfileApiUrl || "").trim();
  const settingsProfileUrl = String(root.dataset.userProfileSettingsProfileUrl || "").trim();
  const settingsCountryCodeUrl = String(root.dataset.userProfileSettingsCountryCodeUrl || "").trim();
  const settingsEmailsUrl = String(root.dataset.userProfileSettingsEmailsUrl || "").trim();
  const membershipHistoryUrlTemplate = String(root.dataset.userProfileMembershipHistoryUrlTemplate || "").trim();
  const membershipRequestUrl = String(root.dataset.userProfileMembershipRequestUrl || "").trim();
  const membershipRequestDetailUrlTemplate = String(root.dataset.userProfileMembershipRequestDetailUrlTemplate || "").trim();
  const membershipSetExpiryUrlTemplate = String(root.dataset.userProfileMembershipSetExpiryUrlTemplate || "").trim();
  const membershipTerminateUrlTemplate = String(root.dataset.userProfileMembershipTerminateUrlTemplate || "").trim();
  const csrfToken = String(root.dataset.userProfileCsrfToken || "").trim();
  const nextUrl = String(root.dataset.userProfileNextUrl || "").trim();
  const membershipNotesSummaryUrl = String(root.dataset.userProfileMembershipNotesSummaryUrl || "").trim();
  const membershipNotesDetailUrl = String(root.dataset.userProfileMembershipNotesDetailUrl || "").trim();
  const membershipNotesAddUrl = String(root.dataset.userProfileMembershipNotesAddUrl || "").trim();
  const membershipNotesCanView = readBoolean(String(root.dataset.userProfileMembershipNotesCanView || "").trim());
  const membershipNotesCanWrite = readBoolean(String(root.dataset.userProfileMembershipNotesCanWrite || "").trim());
  const groupDetailUrlTemplate = String(root.dataset.userProfileGroupDetailUrlTemplate || "").trim();
  const agreementsUrlTemplate = String(root.dataset.userProfileAgreementsUrlTemplate || "").trim();
  if (
    !apiUrl
    || !settingsProfileUrl
    || !settingsCountryCodeUrl
    || !settingsEmailsUrl
    || !membershipHistoryUrlTemplate
    || !membershipRequestUrl
    || !membershipRequestDetailUrlTemplate
    || !membershipSetExpiryUrlTemplate
    || !membershipTerminateUrlTemplate
    || !csrfToken
    || !nextUrl
    || !membershipNotesSummaryUrl
    || !membershipNotesDetailUrl
    || !membershipNotesAddUrl
    || !groupDetailUrlTemplate
    || !agreementsUrlTemplate
  ) {
    return null;
  }
  return {
    apiUrl,
    settingsProfileUrl,
    settingsCountryCodeUrl,
    settingsEmailsUrl,
    membershipHistoryUrlTemplate,
    membershipRequestUrl,
    membershipRequestDetailUrlTemplate,
    membershipManagement: {
      expiryUrlTemplate: membershipSetExpiryUrlTemplate,
      terminateUrlTemplate: membershipTerminateUrlTemplate,
      csrfToken,
      nextUrl,
    },
    membershipNotes: {
      summaryUrl: membershipNotesSummaryUrl,
      detailUrl: membershipNotesDetailUrl,
      addUrl: membershipNotesAddUrl,
      csrfToken,
      nextUrl,
      canView: membershipNotesCanView,
      canWrite: membershipNotesCanWrite,
      targetType: "user",
      target: "",
    },
    groupDetailUrlTemplate,
    agreementsUrlTemplate,
  };
}