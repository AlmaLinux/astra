export interface MembershipRequestDetailBootstrap {
  apiUrl: string;
  csrfToken: string;
  pageTitle: string;
  backLinkUrl: string;
  backLinkLabel: string;
  userProfileUrlTemplate: string;
  organizationDetailUrlTemplate: string;
  contactUrl: string;
  reopenUrl: string;
  noteSummaryUrl: string;
  noteDetailUrl: string;
  noteAddUrl: string;
  noteNextUrl: string;
  notesCanView: boolean;
  notesCanWrite: boolean;
  notesCanVote: boolean;
  approveUrl: string;
  approveOnHoldUrl: string;
  rejectUrl: string;
  rfiUrl: string;
  ignoreUrl: string;
  rescindUrl: string;
  formActionUrl: string;
}

export interface MembershipRequestDetailActor {
  show: boolean;
  username: string;
  full_name: string;
  deleted: boolean;
}

export interface MembershipRequestDetailTarget {
  show: boolean;
  kind: "user" | "organization";
  label: string;
  username: string;
  organization_id: number | null;
  deleted: boolean;
}

export interface MembershipRequestResponseSegment {
  kind: "text" | "link";
  text: string;
  url?: string;
}

export interface MembershipRequestResponseRow {
  question: string;
  answer_text: string;
  segments: MembershipRequestResponseSegment[];
}

export interface MembershipRequestDetailFormField {
  name: string;
  label: string;
  widget: "text" | "textarea";
  value: string;
  required: boolean;
  disabled: boolean;
  help_text: string;
  errors: string[];
}

export interface MembershipRequestDetailForm {
  fields: MembershipRequestDetailFormField[];
  non_field_errors: string[];
}

export interface MembershipRequestDetailPayload {
  viewer: {
    mode: "committee" | "self_service";
  };
  request: {
    id: number;
    status: string;
    requested_at: string | null;
    on_hold_at?: string | null;
    decided_at?: string | null;
    decided_by_username?: string;
    requested_by: MembershipRequestDetailActor;
    requested_for: MembershipRequestDetailTarget;
    membership_type: {
      code?: string;
      name: string;
      category?: string;
    };
    responses: MembershipRequestResponseRow[];
  };
  committee?: {
    reopen: {
      show: boolean;
    };
    compliance_warning?: {
      country_code: string;
      country_label: string;
      message: string;
    } | null;
    actions: {
      canRequestInfo: boolean;
      showOnHoldApprove: boolean;
    };
  };
  self_service?: {
    can_resubmit: boolean;
    can_rescind: boolean;
    committee_email: string;
    user_email: string;
    form: MembershipRequestDetailForm | null;
  };
}

function readBoolData(value: string | undefined): boolean {
  return String(value || "").trim().toLowerCase() === "true";
}

export function readMembershipRequestDetailBootstrap(root: HTMLElement): MembershipRequestDetailBootstrap | null {
  const apiUrl = String(root.dataset.membershipRequestDetailApiUrl || "").trim();
  const csrfToken = String(root.dataset.membershipRequestDetailCsrfToken || "").trim();
  const pageTitle = String(root.dataset.membershipRequestDetailPageTitle || "").trim();
  const backLinkUrl = String(root.dataset.membershipRequestDetailBackLinkUrl || "").trim();
  const backLinkLabel = String(root.dataset.membershipRequestDetailBackLinkLabel || "").trim();
  const userProfileUrlTemplate = String(root.dataset.membershipRequestDetailUserProfileUrlTemplate || "").trim();
  const organizationDetailUrlTemplate = String(root.dataset.membershipRequestDetailOrganizationDetailUrlTemplate || "").trim();
  if (!apiUrl || !csrfToken || !pageTitle || !backLinkUrl || !backLinkLabel || !userProfileUrlTemplate || !organizationDetailUrlTemplate) {
    return null;
  }
  return {
    apiUrl,
    csrfToken,
    pageTitle,
    backLinkUrl,
    backLinkLabel,
    userProfileUrlTemplate,
    organizationDetailUrlTemplate,
    contactUrl: String(root.dataset.membershipRequestDetailContactUrl || "").trim(),
    reopenUrl: String(root.dataset.membershipRequestDetailReopenUrl || "").trim(),
    noteSummaryUrl: String(root.dataset.membershipRequestDetailNoteSummaryUrl || "").trim(),
    noteDetailUrl: String(root.dataset.membershipRequestDetailNoteDetailUrl || "").trim(),
    noteAddUrl: String(root.dataset.membershipRequestDetailNoteAddUrl || "").trim(),
    noteNextUrl: String(root.dataset.membershipRequestDetailNoteNextUrl || "").trim(),
    notesCanView: readBoolData(root.dataset.membershipRequestDetailNotesCanView),
    notesCanWrite: readBoolData(root.dataset.membershipRequestDetailNotesCanWrite),
    notesCanVote: readBoolData(root.dataset.membershipRequestDetailNotesCanVote),
    approveUrl: String(root.dataset.membershipRequestDetailApproveUrl || "").trim(),
    approveOnHoldUrl: String(root.dataset.membershipRequestDetailApproveOnHoldUrl || "").trim(),
    rejectUrl: String(root.dataset.membershipRequestDetailRejectUrl || "").trim(),
    rfiUrl: String(root.dataset.membershipRequestDetailRfiUrl || "").trim(),
    ignoreUrl: String(root.dataset.membershipRequestDetailIgnoreUrl || "").trim(),
    rescindUrl: String(root.dataset.membershipRequestDetailRescindUrl || "").trim(),
    formActionUrl: String(root.dataset.membershipRequestDetailFormActionUrl || "").trim(),
  };
}

export interface MembershipRequestCompatibilityResponse {
  ok: boolean;
  redirect_url: string | null;
  reread_targets: string[];
  field_errors?: Record<string, string[]>;
  non_field_errors?: string[];
}