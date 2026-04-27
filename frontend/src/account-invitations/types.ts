import { formatDjangoDateTime } from "../shared/djangoDateFormatting";

/**
 * Types and bootstrap configuration for account invitations table.
 * Mirrors the membership requests structure for consistency.
 */

export interface AccountInvitationRow {
  invitation_id: number;
  email: string;
  full_name: string;
  note: string;
  invited_by_username: string;
  invited_at: string | null;
  send_count: number;
  last_sent_at: string | null;
  status: "pending" | "accepted" | "dismissed";
  // Accepted-specific
  accepted_at?: string;
  accepted_username?: string;
  freeipa_matched_usernames?: string[];
  // Organization info if linked
  organization_id?: number;
  organization_name?: string;
}

export interface DataTableRow {
  [key: string]: string | number | null | string[] | undefined;
}

export interface AccountInvitationsBootstrap {
  // API URLs (templates with token replacement)
  pendingApiUrl: string;
  acceptedApiUrl: string;
  refreshApiUrl: string;
  resendApiUrl: string;
  dismissApiUrl: string;
  bulkApiUrl: string;
  uploadApiUrl?: string;
  previewApiUrl?: string;
  sendApiUrl?: string;

  // UI URLs
  listPageUrl: string;
  uploadPageUrl?: string;

  // Pagination
  pageSize: number;

  // Permissions
  canManageInvitations: boolean;
  canRefresh: boolean;
  canResend: boolean;
  canDismiss: boolean;
  canBulkAction: boolean;

  // Sentinel token for URL building
  sentinelToken: string;

  // CSRF token
  csrfToken: string;
}

export interface DataTablesQuery {
  draw: number;
  start: number;
  length: number;
  "search[value]": string;
  "search[regex]": string;
  "order[0][column]": number;
  "order[0][dir]": string;
  "order[0][name]": string;
  "columns[0][data]": string;
  "columns[0][name]": string;
  "columns[0][searchable]": string;
  "columns[0][orderable]": string;
  "columns[0][search][value]": string;
  "columns[0][search][regex]": string;
}

export interface DataTablesResponse<T extends DataTableRow = AccountInvitationRow> {
  draw: number;
  recordsTotal: number;
  recordsFiltered: number;
  data: T[];
}

export interface ApiErrorResponse {
  ok: false;
  error: string;
}

export interface ApiSuccessResponse {
  ok: true;
  message: string;
}

export type ApiActionResponse = ApiSuccessResponse | ApiErrorResponse;

/**
 * Helper to read bootstrap configuration from data-* attributes.
 */
export function readAccountInvitationsBootstrap(): AccountInvitationsBootstrap | null {
  const root = document.querySelector("[data-account-invitations-root]");
  if (!root) {
    return null;
  }

  const requiredAttrs = [
    "pendingApiUrl",
    "acceptedApiUrl",
    "listPageUrl",
  ];

  // Check required attributes
  for (const attr of requiredAttrs) {
    const key = `data-account-invitations-${camelCaseToKebabCase(attr)}`;
    if (!root.hasAttribute(key)) {
      console.error(`Missing required attribute: ${key}`);
      return null;
    }
  }

  const bootstrap: AccountInvitationsBootstrap = {
    pendingApiUrl: root.getAttribute("data-account-invitations-pending-api-url") || "",
    acceptedApiUrl: root.getAttribute("data-account-invitations-accepted-api-url") || "",
    refreshApiUrl: root.getAttribute("data-account-invitations-refresh-api-url") || "",
    resendApiUrl: root.getAttribute("data-account-invitations-resend-api-url") || "",
    dismissApiUrl: root.getAttribute("data-account-invitations-dismiss-api-url") || "",
    bulkApiUrl: root.getAttribute("data-account-invitations-bulk-api-url") || "",
    uploadApiUrl: root.getAttribute("data-account-invitations-upload-api-url") || undefined,
    previewApiUrl: root.getAttribute("data-account-invitations-preview-api-url") || undefined,
    sendApiUrl: root.getAttribute("data-account-invitations-send-api-url") || undefined,
    listPageUrl: root.getAttribute("data-account-invitations-list-page-url") || "",
    uploadPageUrl: root.getAttribute("data-account-invitations-upload-page-url") || undefined,
    pageSize: parseInt(root.getAttribute("data-account-invitations-page-size") || "50", 10),
    canManageInvitations: root.getAttribute("data-account-invitations-can-manage") === "true",
    canRefresh: root.getAttribute("data-account-invitations-can-refresh") === "true",
    canResend: root.getAttribute("data-account-invitations-can-resend") === "true",
    canDismiss: root.getAttribute("data-account-invitations-can-dismiss") === "true",
    canBulkAction: root.getAttribute("data-account-invitations-can-bulk-action") === "true",
    sentinelToken: root.getAttribute("data-account-invitations-sentinel-token") || "123456789",
    csrfToken: root.getAttribute("data-account-invitations-csrf-token") || "",
  };

  return bootstrap;
}

/**
 * Convert camelCase to kebab-case.
 */
function camelCaseToKebabCase(str: string): string {
  return str.replace(/([a-z0-9]|(?=[A-Z]))([A-Z])/g, "$1-$2").toLowerCase();
}

/**
 * Replace template token in URL.
 */
export function replaceTemplateToken(url: string, token: string): (id: number | string) => string {
  return (id) => url.replace(token, String(id));
}

/**
 * Format a raw ISO date string using the current Django DATETIME_FORMAT parity.
 */
export function formatDateTime(dateStr: string | null): string {
  return formatDjangoDateTime(dateStr);
}

/**
 * Format a date string to relative format (e.g., "2 hours ago").
 */
export function formatRelativeTime(dateStr: string | null): string {
  if (!dateStr) return "";
  try {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return "just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;

    return formatDateTime(dateStr);
  } catch {
    return dateStr;
  }
}
