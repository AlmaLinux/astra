export interface MembershipAuditLogBootstrap {
  apiUrl: string;
  pageSize: number;
  initialQ: string;
  initialUsername: string;
  initialOrganization: string;
  userProfileUrlTemplate: string;
  organizationDetailUrlTemplate: string;
  membershipRequestDetailUrlTemplate: string;
}

export interface AuditLogTarget {
  kind: "user" | "organization";
  id: number | null;
  label: string;
  secondary_label: string;
  deleted: boolean;
}

export interface AuditLogRequestResponseItem {
  question: string;
  answer_html: string;
}

export interface AuditLogRequestInfo {
  request_id: number;
  responses: AuditLogRequestResponseItem[];
}

export interface AuditLogRow {
  log_id: number;
  created_at_display: string;
  created_at_iso: string;
  actor_username: string;
  target: AuditLogTarget;
  membership_name: string;
  action: string;
  action_display: string;
  expires_display: string;
  request: AuditLogRequestInfo | null;
}

export interface AuditLogDataTablesResponse {
  draw: number;
  recordsTotal: number;
  recordsFiltered: number;
  data: AuditLogRow[];
}

export interface MembershipAuditLogRouteState {
  pathname: string;
  q: string;
  page: number;
  username: string;
  organization: string;
}

function readPositiveInt(value: string | undefined): number | null {
  const parsed = Number.parseInt(value || "", 10);
  if (Number.isNaN(parsed) || parsed < 1) {
    return null;
  }
  return parsed;
}

export function readMembershipAuditLogBootstrap(root: HTMLElement): MembershipAuditLogBootstrap | null {
  const {
    membershipAuditLogApiUrl,
    membershipAuditLogPageSize,
    membershipAuditLogInitialQ,
    membershipAuditLogInitialUsername,
    membershipAuditLogInitialOrganization,
    membershipAuditLogUserProfileUrlTemplate,
    membershipAuditLogOrganizationDetailUrlTemplate,
    membershipAuditLogMembershipRequestDetailUrlTemplate,
  } = root.dataset;

  if (
    !membershipAuditLogApiUrl
    || !membershipAuditLogUserProfileUrlTemplate
    || !membershipAuditLogOrganizationDetailUrlTemplate
    || !membershipAuditLogMembershipRequestDetailUrlTemplate
  ) {
    return null;
  }

  const pageSize = readPositiveInt(membershipAuditLogPageSize);
  if (pageSize === null) {
    return null;
  }

  return {
    apiUrl: membershipAuditLogApiUrl,
    pageSize,
    initialQ: String(membershipAuditLogInitialQ || ""),
    initialUsername: String(membershipAuditLogInitialUsername || ""),
    initialOrganization: String(membershipAuditLogInitialOrganization || ""),
    userProfileUrlTemplate: membershipAuditLogUserProfileUrlTemplate,
    organizationDetailUrlTemplate: membershipAuditLogOrganizationDetailUrlTemplate,
    membershipRequestDetailUrlTemplate: membershipAuditLogMembershipRequestDetailUrlTemplate,
  };
}

export function readMembershipAuditLogRouteState(currentUrl: string): MembershipAuditLogRouteState {
  const url = new URL(currentUrl, "https://example.test");
  const page = Number.parseInt(url.searchParams.get("page") || "1", 10);
  return {
    pathname: url.pathname,
    q: String(url.searchParams.get("q") || ""),
    page: Number.isNaN(page) || page < 1 ? 1 : page,
    username: String(url.searchParams.get("username") || ""),
    organization: String(url.searchParams.get("organization") || ""),
  };
}

export function buildMembershipAuditLogRouteUrl(state: MembershipAuditLogRouteState): string {
  const url = new URL(state.pathname, "https://example.test");
  if (state.q) {
    url.searchParams.set("q", state.q);
  }
  if (state.page > 1) {
    url.searchParams.set("page", String(state.page));
  }
  if (state.username) {
    url.searchParams.set("username", state.username);
  }
  if (state.organization) {
    url.searchParams.set("organization", state.organization);
  }
  return `${url.pathname}${url.search}`;
}
