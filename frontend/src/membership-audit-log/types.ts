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

export interface AuditLogRequestResponseSegment {
  kind: "text" | "link";
  text: string;
  url?: string;
}

export interface AuditLogRequestResponseItem {
  question: string;
  answer_text: string;
  segments: AuditLogRequestResponseSegment[];
}

export interface AuditLogRequestInfo {
  request_id: number;
  responses: AuditLogRequestResponseItem[];
}

export interface AuditLogRow {
  log_id: number;
  created_at: string;
  actor_username: string;
  target: AuditLogTarget;
  membership_name: string;
  action: string;
  expires_at: string | null;
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

const SHORT_WEEKDAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const SHORT_MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const ACTION_LABELS: Record<string, string> = {
  requested: "Requested",
  on_hold: "On Hold",
  resubmitted: "Resubmitted",
  approved: "Approved",
  rejected: "Rejected",
  ignored: "Ignored",
  reopened: "Reopened",
  rescinded: "Rescinded",
  representative_changed: "Representative changed",
  expiry_changed: "Expiry changed",
  terminated: "Terminated",
};

function parseDate(value: string | null | undefined): Date | null {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed;
}

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

export function formatAuditLogDateTime(value: string): string {
  const parsed = parseDate(value);
  if (!parsed) {
    return "";
  }
  const weekday = SHORT_WEEKDAY_NAMES[parsed.getUTCDay()] || "";
  const day = pad2(parsed.getUTCDate());
  const month = SHORT_MONTH_NAMES[parsed.getUTCMonth()] || "";
  const year = parsed.getUTCFullYear();
  const hour = pad2(parsed.getUTCHours());
  const minute = pad2(parsed.getUTCMinutes());
  const second = pad2(parsed.getUTCSeconds());
  return `${weekday}, ${day} ${month} ${year} ${hour}:${minute}:${second} +0000`;
}

export function formatAuditLogAction(action: string): string {
  const normalized = String(action || "").trim().toLowerCase();
  return ACTION_LABELS[normalized] || normalized.replace(/_/g, " ");
}

export function formatAuditLogExpiresAt(value: string | null): string {
  const parsed = parseDate(value);
  if (!parsed) {
    return "";
  }
  const month = SHORT_MONTH_NAMES[parsed.getUTCMonth()] || "";
  return `${month} ${parsed.getUTCDate()}, ${parsed.getUTCFullYear()}`;
}
