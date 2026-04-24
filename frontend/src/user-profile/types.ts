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

export function readUserProfileSummaryBootstrap(root: HTMLElement): UserProfileSummaryBootstrap | null {
  const bootstrapId = String(root.dataset.userProfileBootstrapId || "").trim();
  if (!bootstrapId) {
    return null;
  }

  const script = document.getElementById(bootstrapId);
  if (!(script instanceof HTMLScriptElement) || !script.textContent) {
    return null;
  }

  return JSON.parse(script.textContent) as UserProfileSummaryBootstrap;
}

export function readUserProfileGroupsBootstrap(root: HTMLElement): UserProfileGroupsBootstrap | null {
  const bootstrapId = String(root.dataset.userProfileBootstrapId || "").trim();
  if (!bootstrapId) {
    return null;
  }

  const script = document.getElementById(bootstrapId);
  if (!(script instanceof HTMLScriptElement) || !script.textContent) {
    return null;
  }

  return JSON.parse(script.textContent) as UserProfileGroupsBootstrap;
}