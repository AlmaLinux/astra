export interface UserProfileLinkItem {
  href: string | null;
  text: string;
}

export interface UserProfileSocialProfile {
  label: string;
  title: string;
  icon: string;
  urls: UserProfileLinkItem[];
}

export interface UserProfileSummaryBootstrap {
  fullName: string;
  username: string;
  email: string;
  avatarUrl: string;
  viewerIsMembershipCommittee: boolean;
  profileCountry: string;
  pronouns: string;
  locale: string;
  timezoneName: string;
  currentTimeLabel: string;
  ircNicks: string[];
  socialProfiles: UserProfileSocialProfile[];
  websiteUrls: UserProfileLinkItem[];
  rssUrls: UserProfileLinkItem[];
  rhbzEmail: string;
  githubUsername: string;
  gitlabUsername: string;
  gpgKeys: string[];
  sshKeys: string[];
  isSelf: boolean;
}

export interface UserProfileGroupItem {
  cn: string;
  role: string;
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
  label: string;
  urlLabel: string;
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
  className: string;
}

export interface UserProfileMembershipBadge {
  label: string;
  className: string;
}

export interface UserProfileMembershipManagementAction {
  modalId: string;
  inputId: string;
  expiryActionUrl: string;
  terminateActionUrl: string;
  csrfToken: string;
  nextUrl: string;
  initialValue: string;
  minValue: string;
  currentText: string;
  terminator: string;
}

export interface UserProfileMembershipEntry {
  kind: "membership";
  key: string;
  requestId: number | null;
  membershipType: UserProfileMembershipType;
  badge: UserProfileMembershipBadge;
  memberSinceLabel: string;
  expiresLabel: string;
  expiresTone: "danger" | "muted";
  canRenew: boolean;
  canRequestTierChange: boolean;
  management: UserProfileMembershipManagementAction | null;
}

export interface UserProfilePendingMembershipEntry {
  kind: "pending";
  key: string;
  membershipType: UserProfileMembershipType;
  requestId: number;
  status: string;
  organizationName: string;
  badge: UserProfileMembershipBadge;
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
  notes: UserProfileMembershipNotes | null;
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
  groupDetailUrlTemplate: string;
  agreementsUrlTemplate: string;
}

export function readUserProfileBootstrap(root: HTMLElement): UserProfileBootstrap | null {
  const apiUrl = String(root.dataset.userProfileApiUrl || "").trim();
  const settingsProfileUrl = String(root.dataset.userProfileSettingsProfileUrl || "").trim();
  const settingsCountryCodeUrl = String(root.dataset.userProfileSettingsCountryCodeUrl || "").trim();
  const settingsEmailsUrl = String(root.dataset.userProfileSettingsEmailsUrl || "").trim();
  const membershipHistoryUrlTemplate = String(root.dataset.userProfileMembershipHistoryUrlTemplate || "").trim();
  const membershipRequestUrl = String(root.dataset.userProfileMembershipRequestUrl || "").trim();
  const membershipRequestDetailUrlTemplate = String(root.dataset.userProfileMembershipRequestDetailUrlTemplate || "").trim();
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
    groupDetailUrlTemplate,
    agreementsUrlTemplate,
  };
}