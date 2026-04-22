export interface MembershipRequestsBootstrap {
  clearFilterUrl: string;
  pendingApiUrl: string;
  onHoldApiUrl: string;
  bulkUrl?: string;
  requestIdSentinel: string;
  requestDetailTemplate: string;
  approveTemplate: string;
  approveOnHoldTemplate: string;
  rejectTemplate: string;
  requestInfoTemplate: string;
  ignoreTemplate: string;
  noteAddTemplate: string;
  noteSummaryTemplate: string;
  noteDetailTemplate: string;
  userProfileTemplate: string;
  organizationDetailTemplate: string;
  nextUrl?: string;
  csrfToken?: string;
  canRequestInfo: boolean;
  notesCanView: boolean;
  notesCanWrite: boolean;
  notesCanVote: boolean;
}

export interface MembershipRequestTarget {
  kind: "user" | "organization";
  label: string;
  secondary_label: string;
  username?: string;
  organization_id?: number | null;
  deleted: boolean;
}

export interface MembershipRequestActor {
  show: boolean;
  username: string;
  full_name: string;
  deleted: boolean;
}

export interface MembershipTypeSummary {
  id: string;
  code: string;
  name: string;
  category: string;
}

export interface MembershipRequestResponseItem {
  question: string;
  answer_html: string;
}

export interface MembershipRequestRow {
  request_id: number;
  status: "pending" | "on_hold" | string;
  requested_at: string;
  on_hold_since: string | null;
  target: MembershipRequestTarget;
  requested_by: MembershipRequestActor;
  membership_type: MembershipTypeSummary;
  is_renewal: boolean;
  responses: MembershipRequestResponseItem[];
}

export interface PendingFilterOption {
  value: string;
  label: string;
  count: number;
}

export interface NoteSummary {
  note_count: number;
  approvals: number;
  disapprovals: number;
  current_user_vote: string | null;
}

export interface ContactedEmailLog {
  date_display: string;
  status: string;
  message: string;
  exception_type?: string;
}

export interface ContactedEmail {
  email_id?: number;
  to?: string[];
  cc?: string[];
  bcc?: string[];
  subject?: string;
  from_email?: string;
  reply_to?: string;
  recipient_delivery_summary?: string;
  recipient_delivery_summary_note?: string;
  headers?: [string, string][];
  html?: string;
  text?: string;
  logs?: ContactedEmailLog[];
}

export interface RequestResubmittedDiffRow {
  question: string;
  old_value: string;
  new_value: string;
}

export interface NoteEntry {
  kind: "message" | "action";
  rendered_html?: string;
  is_self?: boolean;
  is_custos?: boolean;
  bubble_style?: string;
  icon?: string;
  label?: string;
  note_id?: number;
  contacted_email?: ContactedEmail;
  request_resubmitted_diff_rows?: RequestResubmittedDiffRow[];
}

export interface NoteGroup {
  username: string;
  display_username: string;
  is_self: boolean;
  is_custos: boolean;
  avatar_kind: string;
  avatar_url: string;
  timestamp_display: string;
  membership_request_id?: number;
  membership_request_url?: string;
  entries: NoteEntry[];
}

export interface NoteDetails {
  groups: NoteGroup[];
}

export interface MembershipRequestsRouteState {
  pathname: string;
  filter: string;
  pendingPage: number;
  onHoldPage: number;
}

export function replaceTemplateToken(template: string, token: string, value: string | number): string {
  return template.split(token).join(String(value));
}

export function readMembershipRequestsRouteState(currentUrl: string): MembershipRequestsRouteState {
  const url = new URL(currentUrl, "https://example.test");
  const pendingPage = Number.parseInt(url.searchParams.get("pending_page") || "1", 10);
  const onHoldPage = Number.parseInt(url.searchParams.get("on_hold_page") || "1", 10);
  return {
    pathname: url.pathname,
    filter: String(url.searchParams.get("filter") || "all"),
    pendingPage: Number.isNaN(pendingPage) || pendingPage < 1 ? 1 : pendingPage,
    onHoldPage: Number.isNaN(onHoldPage) || onHoldPage < 1 ? 1 : onHoldPage,
  };
}

export function buildMembershipRequestsRouteUrl(state: MembershipRequestsRouteState): string {
  const url = new URL(state.pathname, "https://example.test");
  if (state.filter && state.filter !== "all") {
    url.searchParams.set("filter", state.filter);
  }
  if (state.pendingPage > 1) {
    url.searchParams.set("pending_page", String(state.pendingPage));
  }
  if (state.onHoldPage > 1) {
    url.searchParams.set("on_hold_page", String(state.onHoldPage));
  }
  return `${url.pathname}${url.search}`;
}

export function canLinkMembershipRequestTarget(target: MembershipRequestTarget): boolean {
  if (target.kind === "organization" && target.deleted) {
    return false;
  }
  if (target.kind === "user") {
    return Boolean(target.username);
  }
  return Boolean(target.organization_id);
}

export function membershipRequestTargetContext(target: MembershipRequestTarget): string {
  return target.secondary_label || target.label;
}

export function membershipRequestActorLabel(actor: MembershipRequestActor): string {
  if (actor.full_name) {
    return `${actor.full_name} (${actor.username})`;
  }
  return actor.username;
}

export function formatDateTime(value: string | null): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString();
}

export function formatLegacyDateTime(value: string | null): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const year = String(date.getFullYear());
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${year}-${month}-${day} ${hours}:${minutes}`;
}

export function formatRelativeAgo(value: string | null, nowMs: number = Date.now()): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const diffMs = Math.max(0, nowMs - date.getTime());
  const minuteMs = 60 * 1000;
  const hourMs = 60 * minuteMs;
  const dayMs = 24 * hourMs;
  if (diffMs < hourMs) {
    const minutes = Math.max(1, Math.floor(diffMs / minuteMs));
    return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  }
  if (diffMs < dayMs) {
    const hours = Math.max(1, Math.floor(diffMs / hourMs));
    return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  }
  const days = Math.max(1, Math.floor(diffMs / dayMs));
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

export interface PaginationWindow {
  pageNumbers: number[];
  showFirst: boolean;
  showLast: boolean;
}

export function buildPaginationWindow(totalPages: number, currentPage: number): PaginationWindow {
  if (totalPages <= 10) {
    return {
      pageNumbers: Array.from({ length: totalPages }, (_unused, index) => index + 1),
      showFirst: false,
      showLast: false,
    };
  }

  const start = Math.max(1, currentPage - 2);
  const end = Math.min(totalPages, currentPage + 2);
  const pageNumbers: number[] = [];
  for (let pageNumber = start; pageNumber <= end; pageNumber += 1) {
    pageNumbers.push(pageNumber);
  }
  return {
    pageNumbers,
    showFirst: !pageNumbers.includes(1),
    showLast: !pageNumbers.includes(totalPages),
  };
}

function readBoolean(value: string | undefined): boolean {
  return String(value || "").toLowerCase() === "true";
}

export function readMembershipRequestsBootstrap(root: HTMLElement): MembershipRequestsBootstrap | null {
  const {
    membershipRequestsClearFilterUrl,
    membershipRequestsPendingApiUrl,
    membershipRequestsOnHoldApiUrl,
    membershipRequestsBulkUrl,
    membershipRequestIdSentinel,
    membershipRequestDetailTemplate,
    membershipRequestApproveTemplate,
    membershipRequestApproveOnHoldTemplate,
    membershipRequestRejectTemplate,
    membershipRequestRfiTemplate,
    membershipRequestIgnoreTemplate,
    membershipRequestNoteAddTemplate,
    membershipRequestNoteSummaryTemplate,
    membershipRequestNoteDetailTemplate,
    membershipUserProfileTemplate,
    membershipOrganizationDetailTemplate,
    membershipRequestsNextUrl,
    membershipRequestsCsrfToken,
    membershipRequestsCanRequestInfo,
    membershipRequestsNotesCanView,
    membershipRequestsNotesCanWrite,
    membershipRequestsNotesCanVote,
  } = root.dataset;

  if (
    !membershipRequestsClearFilterUrl
    || !membershipRequestsPendingApiUrl
    || !membershipRequestsOnHoldApiUrl
    || !membershipRequestIdSentinel
    || !membershipRequestDetailTemplate
    || !membershipRequestApproveTemplate
    || !membershipRequestApproveOnHoldTemplate
    || !membershipRequestRejectTemplate
    || !membershipRequestRfiTemplate
    || !membershipRequestIgnoreTemplate
    || !membershipRequestNoteAddTemplate
    || !membershipRequestNoteSummaryTemplate
    || !membershipRequestNoteDetailTemplate
    || !membershipUserProfileTemplate
    || !membershipOrganizationDetailTemplate
  ) {
    return null;
  }

  return {
    clearFilterUrl: membershipRequestsClearFilterUrl,
    pendingApiUrl: membershipRequestsPendingApiUrl,
    onHoldApiUrl: membershipRequestsOnHoldApiUrl,
    bulkUrl: membershipRequestsBulkUrl,
    requestIdSentinel: membershipRequestIdSentinel,
    requestDetailTemplate: membershipRequestDetailTemplate,
    approveTemplate: membershipRequestApproveTemplate,
    approveOnHoldTemplate: membershipRequestApproveOnHoldTemplate,
    rejectTemplate: membershipRequestRejectTemplate,
    requestInfoTemplate: membershipRequestRfiTemplate,
    ignoreTemplate: membershipRequestIgnoreTemplate,
    noteAddTemplate: membershipRequestNoteAddTemplate,
    noteSummaryTemplate: membershipRequestNoteSummaryTemplate,
    noteDetailTemplate: membershipRequestNoteDetailTemplate,
    userProfileTemplate: membershipUserProfileTemplate,
    organizationDetailTemplate: membershipOrganizationDetailTemplate,
    nextUrl: membershipRequestsNextUrl,
    csrfToken: membershipRequestsCsrfToken,
    canRequestInfo: readBoolean(membershipRequestsCanRequestInfo),
    notesCanView: readBoolean(membershipRequestsNotesCanView),
    notesCanWrite: readBoolean(membershipRequestsNotesCanWrite),
    notesCanVote: readBoolean(membershipRequestsNotesCanVote),
  };
}