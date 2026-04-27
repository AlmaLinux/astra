export interface MembershipRequestFormOption {
  value: string;
  label: string;
  selected: boolean;
  disabled: boolean;
  category: string;
}

export interface MembershipRequestFormOptionGroup {
  label: string | null;
  options: MembershipRequestFormOption[];
}

export interface MembershipRequestFormField {
  name: string;
  id: string;
  label: string;
  widget: "text" | "textarea" | "select";
  value: string;
  required: boolean;
  disabled: boolean;
  helpText: string;
  errors: string[];
  attrs: Record<string, string>;
  optionGroups?: MembershipRequestFormOptionGroup[];
}

export interface MembershipRequestFormPayload {
  organization: { id: number; name: string } | null;
  noTypesAvailable: boolean;
  prefillTypeUnavailableName: string | null;
  form: {
    isBound: boolean;
    nonFieldErrors: string[];
    fields: MembershipRequestFormField[];
  };
}

interface MembershipRequestFormApiPayload {
  organization: { id: number; name: string } | null;
  no_types_available: boolean;
  prefill_type_unavailable_name: string | null;
  form: {
    is_bound: boolean;
    non_field_errors: string[];
    fields: Array<{
      name: string;
      id: string;
      label: string;
      widget: "text" | "textarea" | "select";
      value: string;
      required: boolean;
      disabled: boolean;
      help_text: string;
      errors: string[];
      attrs: Record<string, string>;
      option_groups?: MembershipRequestFormOptionGroup[];
    }>;
  };
}

export interface MembershipRequestFormBootstrap {
  apiUrl: string;
  cancelUrl: string;
  submitUrl: string;
  pageTitle: string;
  privacyPolicyUrl: string;
  csrfToken?: string;
  initialPayload: MembershipRequestFormPayload | null;
}

function normalizeApiPayload(payload: MembershipRequestFormApiPayload): MembershipRequestFormPayload {
  return {
    organization: payload.organization,
    noTypesAvailable: payload.no_types_available,
    prefillTypeUnavailableName: payload.prefill_type_unavailable_name,
    form: {
      isBound: payload.form.is_bound,
      nonFieldErrors: payload.form.non_field_errors,
      fields: payload.form.fields.map((field) => ({
        name: field.name,
        id: field.id,
        label: field.label,
        widget: field.widget,
        value: field.value,
        required: field.required,
        disabled: field.disabled,
        helpText: field.help_text,
        errors: field.errors,
        attrs: field.attrs,
        optionGroups: field.option_groups || [],
      })),
    },
  };
}

function readInitialPayload(root: HTMLElement): MembershipRequestFormPayload | null {
  const script = root.querySelector<HTMLScriptElement>("script[data-membership-request-form-initial-payload]");
  if (!script?.textContent) {
    return null;
  }
  return normalizeApiPayload(JSON.parse(script.textContent) as MembershipRequestFormApiPayload);
}

export async function fetchMembershipRequestFormPayload(apiUrl: string): Promise<MembershipRequestFormPayload> {
  const response = await fetch(apiUrl, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new Error("Unable to load membership request form.");
  }
  return normalizeApiPayload((await response.json()) as MembershipRequestFormApiPayload);
}

export function readMembershipRequestFormBootstrap(root: HTMLElement): MembershipRequestFormBootstrap | null {
  const apiUrl = String(root.dataset.membershipRequestFormApiUrl || "").trim();
  const cancelUrl = String(root.dataset.membershipRequestFormCancelUrl || "").trim();
  const submitUrl = String(root.dataset.membershipRequestFormSubmitUrl || "").trim();
  const pageTitle = String(root.dataset.membershipRequestFormPageTitle || "").trim();
  const privacyPolicyUrl = String(root.dataset.membershipRequestFormPrivacyPolicyUrl || "").trim();
  const csrfToken = String(root.dataset.membershipRequestFormCsrfToken || "").trim();
  const initialPayload = readInitialPayload(root);
  if (!cancelUrl || !submitUrl || !pageTitle || !privacyPolicyUrl || (!apiUrl && initialPayload === null)) {
    return null;
  }
  return {
    apiUrl,
    cancelUrl,
    submitUrl,
    pageTitle,
    privacyPolicyUrl,
    csrfToken,
    initialPayload,
  };
}