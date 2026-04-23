export interface MembershipAuditLogBootstrap {
  apiUrl: string;
  pageSize: number;
  initialQ: string;
  initialUsername: string;
  initialOrganization: string;
}

export interface AuditLogTarget {
  kind: "user" | "organization";
  label: string;
  secondary_label: string;
  deleted: boolean;
  url: string;
}

export interface AuditLogRequestResponseItem {
  question: string;
  answer_html: string;
}

export interface AuditLogRequestInfo {
  request_id: number;
  url: string;
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

function readPositiveInt(value: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(value || "", 10);
  if (Number.isNaN(parsed) || parsed < 1) {
    return fallback;
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
  } = root.dataset;

  if (!membershipAuditLogApiUrl) {
    return null;
  }

  return {
    apiUrl: membershipAuditLogApiUrl,
    pageSize: readPositiveInt(membershipAuditLogPageSize, 50),
    initialQ: String(membershipAuditLogInitialQ || ""),
    initialUsername: String(membershipAuditLogInitialUsername || ""),
    initialOrganization: String(membershipAuditLogInitialOrganization || ""),
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
