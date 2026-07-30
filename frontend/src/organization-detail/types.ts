// The membership entries below are the shared membership-panel wire shape
// (see core/membership_payloads.py), so they are camelCase like the user
// profile payload. The organization-only envelope keys stay snake_case.
export interface OrganizationDetailMembershipType {
  name: string;
  code: string;
  description: string;
}

export interface OrganizationDetailMembership {
  kind: "membership";
  key: string;
  requestId: number | null;
  membershipType: OrganizationDetailMembershipType;
  createdAt: string | null;
  expiresAt: string | null;
  isExpiringSoon: boolean;
  canRenew: boolean;
  renewalMembershipTypeCode: string;
  canRequestTierChange: boolean;
  tierChangeMembershipTypeCode: string;
  canManage: boolean;
}

export interface OrganizationDetailPendingMembership {
  kind: "pending";
  key: string;
  requestId: number;
  status: string;
  membershipType: OrganizationDetailMembershipType;
  organizationName: string;
}

export interface OrganizationDetailRepresentative {
  username: string;
  full_name: string;
}

export interface OrganizationDetailContactGroup {
  key: string;
  name: string;
  email: string;
  phone: string;
}

export interface OrganizationDetailAddress {
  street: string;
  city: string;
  state: string;
  postal_code: string;
  country_code: string;
}

export interface OrganizationDetailNotes {
  summaryUrl: string;
  detailUrl: string;
  addUrl: string;
  csrfToken: string;
  nextUrl: string;
  canView: boolean;
  canWrite: boolean;
}

export interface OrganizationDetailOrganization {
  id: number;
  name: string;
  status: string;
  is_representative?: boolean;
  website: string;
  logo_url: string;
  can_view_history?: boolean;
  can_request_any?: boolean;
  can_request_membership?: boolean;
  memberships: OrganizationDetailMembership[];
  pending_memberships: OrganizationDetailPendingMembership[];
  representative: OrganizationDetailRepresentative;
  contact_groups: OrganizationDetailContactGroup[];
  address: OrganizationDetailAddress;
}

export interface OrganizationDetailResponse {
  organization: OrganizationDetailOrganization;
}

export interface OrganizationDetailBootstrap {
  apiUrl: string;
  membershipRequestDetailTemplate: string;
  membershipRequestUrl: string;
  membershipHistoryUrl: string;
  sponsorshipSetExpiryUrlTemplate: string;
  sponsorshipTerminateUrlTemplate: string;
  csrfToken: string;
  nextUrl: string;
  expiryMinDate: string;
  userProfileUrlTemplate: string;
  sendMailUrlTemplate: string;
  membershipNotes: OrganizationDetailNotes | null;
}

function readBooleanFlag(value: string | undefined): boolean {
  return String(value || "").trim().toLowerCase() === "true";
}

export function readOrganizationDetailBootstrap(root: HTMLElement): OrganizationDetailBootstrap | null {
  const apiUrl = String(root.dataset.organizationDetailApiUrl || "").trim();
  const membershipRequestDetailTemplate = String(root.dataset.organizationDetailMembershipRequestDetailTemplate || "").trim();
  const membershipRequestUrl = String(root.dataset.organizationDetailMembershipRequestUrl || "").trim();
  const membershipHistoryUrl = String(root.dataset.organizationDetailMembershipHistoryUrl || "").trim();
  const sponsorshipSetExpiryUrlTemplate = String(root.dataset.organizationDetailSponsorshipSetExpiryUrlTemplate || "").trim();
  const sponsorshipTerminateUrlTemplate = String(root.dataset.organizationDetailSponsorshipTerminateUrlTemplate || "").trim();
  const csrfToken = String(root.dataset.organizationDetailCsrfToken || "").trim();
  const nextUrl = String(root.dataset.organizationDetailNextUrl || "").trim();
  const expiryMinDate = String(root.dataset.organizationDetailExpiryMinDate || "").trim();
  const userProfileUrlTemplate = String(root.dataset.organizationDetailUserProfileUrlTemplate || "").trim();
  const sendMailUrlTemplate = String(root.dataset.organizationDetailSendMailUrlTemplate || "").trim();
  if (
    !apiUrl
    || !membershipRequestDetailTemplate
    || !membershipRequestUrl
    || !membershipHistoryUrl
    || !sponsorshipSetExpiryUrlTemplate
    || !sponsorshipTerminateUrlTemplate
    || !csrfToken
    || !nextUrl
    || !expiryMinDate
    || !userProfileUrlTemplate
    || !sendMailUrlTemplate
  ) {
    return null;
  }

  let membershipNotes: OrganizationDetailNotes | null = null;
  const summaryUrl = String(root.dataset.organizationDetailMembershipNotesSummaryUrl || "").trim();
  const detailUrl = String(root.dataset.organizationDetailMembershipNotesDetailUrl || "").trim();
  const addUrl = String(root.dataset.organizationDetailMembershipNotesAddUrl || "").trim();
  const notesCsrfToken = String(root.dataset.organizationDetailMembershipNotesCsrfToken || "").trim();
  const notesNextUrl = String(root.dataset.organizationDetailMembershipNotesNextUrl || "").trim();
  if (summaryUrl && detailUrl && addUrl && notesCsrfToken && notesNextUrl) {
    membershipNotes = {
      summaryUrl,
      detailUrl,
      addUrl,
      csrfToken: notesCsrfToken,
      nextUrl: notesNextUrl,
      canView: readBooleanFlag(root.dataset.organizationDetailMembershipNotesCanView),
      canWrite: readBooleanFlag(root.dataset.organizationDetailMembershipNotesCanWrite),
    };
  }

  return {
    apiUrl,
    membershipRequestDetailTemplate,
    membershipRequestUrl,
    membershipHistoryUrl,
    sponsorshipSetExpiryUrlTemplate,
    sponsorshipTerminateUrlTemplate,
    csrfToken,
    nextUrl,
    expiryMinDate,
    userProfileUrlTemplate,
    sendMailUrlTemplate,
    membershipNotes,
  };
}
