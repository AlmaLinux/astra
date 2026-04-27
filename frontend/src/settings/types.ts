export interface SettingsFieldOption {
  value: string;
  label: string;
}

export interface SettingsField {
  name: string;
  id: string;
  widget: "hidden" | "password" | "email" | "textarea" | "select" | "checkbox" | "file" | "text";
  value: string;
  required: boolean;
  disabled: boolean;
  errors: string[];
  attrs: Record<string, string>;
  checked?: boolean;
  options?: SettingsFieldOption[];
}

export interface SettingsForm {
  isBound: boolean;
  nonFieldErrors: string[];
  fields: SettingsField[];
}

export interface SettingsRouteConfig {
  profileUrl: string;
  emailsUrl: string;
  keysUrl: string;
  securityUrl: string;
  privacyUrl: string;
  membershipUrl: string;
  agreementsUrl: string;
  userProfileUrl: string;
  avatarUploadUrl?: string;
  avatarDeleteUrl?: string;
  accountDeletionSubmitUrl: string;
  otpEnableUrl: string;
  otpDisableUrl: string;
  otpDeleteUrl: string;
  otpRenameUrl: string;
  membershipTerminateUrlTemplate: string;
  groupDetailUrlTemplate: string;
  agreementDetailUrlTemplate: string;
}

export interface SettingsPayload {
  activeTab: string;
  tabs: string[];
  profile: {
    form: SettingsForm;
    avatarUrl: string;
    avatarProvider: string;
    avatarIsLocal: boolean;
    avatarManageUrl: string;
    highlight: string;
    chatDefaults: {
      mattermostServer: string;
      mattermostTeam: string;
      ircServer: string;
      matrixServer: string;
    };
    localeOptions: string[];
    timezoneOptions: string[];
  };
  emails: {
    form: SettingsForm;
    emailIsBlacklisted: boolean;
  };
  keys: {
    form: SettingsForm;
  };
  security: {
    usingOtp: boolean;
    password: { form: SettingsForm };
    otpAdd: { form: SettingsForm };
    otpConfirm: { form: SettingsForm; otpUri: string | null; otpQrPngB64: string | null };
    otpTokens: Array<{ description: string; uniqueId: string; disabled: boolean }>;
  };
  privacy: {
    form: SettingsForm;
    accountDeletionForm: SettingsForm | null;
    activeDeletionRequest: { status: string } | null;
    privacyWarnings: string[];
  };
  agreements?: {
    agreement: { cn: string; descriptionMarkdown: string; groups: string[]; signed: boolean } | null;
    agreements: Array<{ cn: string; groups: string[]; signed: boolean }>;
  };
  membership: {
    activeMemberships: Array<{
      membershipTypeCode: string;
      membershipTypeName: string;
      createdAt: string;
      expiresAt: string | null;
      terminationForm: SettingsForm;
    }>;
    history: Array<{
      membershipTypeName: string;
      createdAt: string;
      action: string;
    }>;
  };
}

export interface SettingsBootstrap {
  apiUrl: string;
  submitUrl: string;
  csrfToken: string;
  routeConfig: SettingsRouteConfig;
  initialPayload: SettingsPayload | null;
}

export interface SettingsEmailValidationPayload {
  email: string;
  emailType: "primary" | "bugzilla";
  isValid: boolean;
}

export interface SettingsEmailValidationBootstrap {
  apiUrl: string;
  submitUrl: string;
  cancelUrl: string;
  csrfToken: string;
  username: string;
  routeConfig: SettingsRouteConfig;
  visibleTabs: string[];
  initialPayload: SettingsEmailValidationPayload | null;
}

interface SettingsFieldApiPayload {
  name: string;
  id: string;
  widget: SettingsField["widget"];
  value: string;
  required: boolean;
  disabled: boolean;
  errors: string[];
  attrs: Record<string, string>;
  checked?: boolean;
  options?: SettingsFieldOption[];
}

interface SettingsFormApiPayload {
  is_bound: boolean;
  non_field_errors: string[];
  fields: SettingsFieldApiPayload[];
}

function parseJsonScript<T>(root: HTMLElement, selector: string): T | null {
  const script = root.querySelector<HTMLScriptElement>(selector);
  if (!script?.textContent) {
    return null;
  }
  return JSON.parse(script.textContent) as T;
}

function normalizeForm(payload: SettingsFormApiPayload): SettingsForm {
  return {
    isBound: payload.is_bound,
    nonFieldErrors: payload.non_field_errors,
    fields: payload.fields.map((field) => ({
      name: field.name,
      id: field.id,
      widget: field.widget,
      value: Array.isArray(field.value) ? field.value.join("\n") : String(field.value ?? ""),
      required: field.required,
      disabled: field.disabled,
      errors: field.errors,
      attrs: field.attrs,
      checked: field.checked,
      options: field.options,
    })),
  };
}

function normalizePayload(payload: any): SettingsPayload {
  const profileChatDefaults = payload.profile.chat_defaults || payload.profile.chatDefaults || {};
  return {
    activeTab: payload.active_tab,
    tabs: payload.tabs,
    profile: {
      form: normalizeForm(payload.profile.form),
      avatarUrl: payload.profile.avatar_url || "",
      avatarProvider: payload.profile.avatar_provider || "",
      avatarIsLocal: Boolean(payload.profile.avatar_is_local),
      avatarManageUrl: payload.profile.avatar_manage_url || "",
      highlight: payload.profile.highlight || "",
      chatDefaults: {
        mattermostServer: profileChatDefaults.mattermost_server || profileChatDefaults.mattermostServer || "",
        mattermostTeam: profileChatDefaults.mattermost_team || profileChatDefaults.mattermostTeam || "",
        ircServer: profileChatDefaults.irc_server || profileChatDefaults.ircServer || "",
        matrixServer: profileChatDefaults.matrix_server || profileChatDefaults.matrixServer || "",
      },
      localeOptions: payload.profile.locale_options || [],
      timezoneOptions: payload.profile.timezone_options || [],
    },
    emails: {
      form: normalizeForm(payload.emails.form),
      emailIsBlacklisted: Boolean(payload.emails.email_is_blacklisted),
    },
    keys: { form: normalizeForm(payload.keys.form) },
    security: {
      usingOtp: Boolean(payload.security.using_otp),
      password: { form: normalizeForm(payload.security.password.form) },
      otpAdd: { form: normalizeForm(payload.security.otp_add.form) },
      otpConfirm: {
        form: normalizeForm(payload.security.otp_confirm.form),
        otpUri: payload.security.otp_confirm.otp_uri,
        otpQrPngB64: payload.security.otp_confirm.otp_qr_png_b64,
      },
      otpTokens: (payload.security.otp_tokens || []).map((token: any) => ({
        description: token.description || "",
        uniqueId: token.unique_id || "",
        disabled: Boolean(token.disabled),
      })),
    },
    privacy: {
      form: normalizeForm(payload.privacy.form),
      accountDeletionForm: payload.privacy.account_deletion_form ? normalizeForm(payload.privacy.account_deletion_form) : null,
      activeDeletionRequest: payload.privacy.active_deletion_request
        ? { status: payload.privacy.active_deletion_request.status }
        : null,
      privacyWarnings: payload.privacy.privacy_warnings || [],
    },
    agreements: payload.agreements
      ? {
          agreement: payload.agreements.agreement
            ? {
                cn: payload.agreements.agreement.cn,
                descriptionMarkdown: payload.agreements.agreement.description_markdown,
                groups: payload.agreements.agreement.groups || [],
                signed: Boolean(payload.agreements.agreement.signed),
              }
            : null,
          agreements: (payload.agreements.agreements || []).map((agreement: any) => ({
            cn: agreement.cn,
            groups: agreement.groups || [],
            signed: Boolean(agreement.signed),
          })),
        }
      : undefined,
    membership: {
      activeMemberships: (payload.membership.active_memberships || []).map((membership: any) => ({
        membershipTypeCode: membership.membership_type_code,
        membershipTypeName: membership.membership_type_name,
        createdAt: membership.created_at,
        expiresAt: membership.expires_at,
        terminationForm: normalizeForm(membership.termination_form),
      })),
      history: (payload.membership.history || []).map((entry: any) => ({
        membershipTypeName: entry.membership_type_name,
        createdAt: entry.created_at,
        action: entry.action,
      })),
    },
  };
}

function normalizeSettingsEmailValidationPayload(payload: any): SettingsEmailValidationPayload {
  return {
    email: String(payload.email || ""),
    emailType: payload.email_type === "bugzilla" ? "bugzilla" : "primary",
    isValid: Boolean(payload.is_valid),
  };
}

function normalizeRouteConfig(payload: any): SettingsRouteConfig {
  return {
    profileUrl: payload.profile_url,
    emailsUrl: payload.emails_url,
    keysUrl: payload.keys_url,
    securityUrl: payload.security_url,
    privacyUrl: payload.privacy_url,
    membershipUrl: payload.membership_url,
    agreementsUrl: payload.agreements_url,
    userProfileUrl: payload.user_profile_url,
    avatarUploadUrl: payload.avatar_upload_url,
    avatarDeleteUrl: payload.avatar_delete_url,
    accountDeletionSubmitUrl: payload.account_deletion_submit_url,
    otpEnableUrl: payload.otp_enable_url,
    otpDisableUrl: payload.otp_disable_url,
    otpDeleteUrl: payload.otp_delete_url,
    otpRenameUrl: payload.otp_rename_url,
    membershipTerminateUrlTemplate: payload.membership_terminate_url_template,
    groupDetailUrlTemplate: payload.group_detail_url_template,
    agreementDetailUrlTemplate: payload.agreement_detail_url_template,
  };
}

export async function fetchSettingsPayload(apiUrl: string): Promise<SettingsPayload> {
  const response = await fetch(apiUrl, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new Error("Unable to load settings.");
  }
  return normalizePayload(await response.json());
}

export async function fetchSettingsEmailValidationPayload(apiUrl: string): Promise<SettingsEmailValidationPayload> {
  const response = await fetch(apiUrl, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new Error("Unable to load email validation.");
  }
  return normalizeSettingsEmailValidationPayload(await response.json());
}

export function readSettingsBootstrap(root: HTMLElement): SettingsBootstrap | null {
  const apiUrl = String(root.dataset.settingsApiUrl || "").trim();
  const submitUrl = String(root.dataset.settingsSubmitUrl || "").trim();
  const csrfToken = String(root.dataset.settingsCsrfToken || "").trim();
  const initialPayloadRaw = parseJsonScript<any>(root, "#settings-initial-payload");
  const routeConfigRaw = parseJsonScript<any>(root, "#settings-route-config");
  if (!submitUrl || routeConfigRaw === null || (!apiUrl && initialPayloadRaw === null)) {
    return null;
  }
  return {
    apiUrl,
    submitUrl,
    csrfToken,
    routeConfig: normalizeRouteConfig(routeConfigRaw),
    initialPayload: initialPayloadRaw ? normalizePayload(initialPayloadRaw) : null,
  };
}

export function readSettingsEmailValidationBootstrap(root: HTMLElement): SettingsEmailValidationBootstrap | null {
  const apiUrl = String(root.dataset.settingsEmailValidationApiUrl || "").trim();
  const submitUrl = String(root.dataset.settingsEmailValidationSubmitUrl || "").trim();
  const cancelUrl = String(root.dataset.settingsEmailValidationCancelUrl || "").trim();
  const csrfToken = String(root.dataset.settingsEmailValidationCsrfToken || "").trim();
  const username = String(root.dataset.settingsEmailValidationUsername || "").trim();
  const initialPayloadRaw = parseJsonScript<any>(root, "#settings-email-validation-initial-payload");
  const routeConfigRaw = parseJsonScript<any>(root, "#settings-email-validation-route-config");
  const visibleTabs = parseJsonScript<string[]>(root, "#settings-email-validation-tabs");
  if (!submitUrl || !cancelUrl || !username || routeConfigRaw === null || visibleTabs === null || (!apiUrl && initialPayloadRaw === null)) {
    return null;
  }
  return {
    apiUrl,
    submitUrl,
    cancelUrl,
    csrfToken,
    username,
    routeConfig: normalizeRouteConfig(routeConfigRaw),
    visibleTabs,
    initialPayload: initialPayloadRaw ? normalizeSettingsEmailValidationPayload(initialPayloadRaw) : null,
  };
}