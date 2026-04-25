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
  profileEditUrl: string;
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
  settingsUrl: string;
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
  url: string;
  urlLabel: string;
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
  url: string | null;
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
  membershipType: UserProfileMembershipType;
  badge: UserProfileMembershipBadge;
  memberSinceLabel: string;
  expiresLabel: string;
  expiresTone: "danger" | "muted";
  renewUrl: string;
  tierChangeUrl: string;
  management: UserProfileMembershipManagementAction | null;
}

export interface UserProfilePendingMembershipEntry {
  kind: "pending";
  key: string;
  membershipType: UserProfileMembershipType;
  requestId: number;
  requestUrl: string;
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
  historyUrl: string;
  requestUrl: string;
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
}

export function readUserProfileBootstrap(root: HTMLElement): UserProfileBootstrap | null {
  const apiUrl = String(root.dataset.userProfileApiUrl || "").trim();
  if (!apiUrl) {
    return null;
  }
  return { apiUrl };
}