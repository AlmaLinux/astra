type AuthRecoveryWidget = "text" | "email" | "password" | "hidden";

export interface AuthRecoveryFormField {
  name: string;
  id: string;
  widget: AuthRecoveryWidget;
  value: string;
  required: boolean;
  disabled: boolean;
  errors: string[];
  attrs: Record<string, string>;
}

export interface AuthRecoveryFormPayload {
  isBound: boolean;
  nonFieldErrors: string[];
  fields: AuthRecoveryFormField[];
}

export interface PasswordResetRequestPayload {
  form: AuthRecoveryFormPayload;
}

export interface PasswordResetConfirmPayload {
  username: string;
  hasOtp: boolean;
  form: AuthRecoveryFormPayload;
}

export interface PasswordExpiredPayload {
  form: AuthRecoveryFormPayload;
}

export interface OtpSyncPayload {
  form: AuthRecoveryFormPayload;
}

interface AuthRecoveryFormApiPayload {
  is_bound: boolean;
  non_field_errors: string[];
  fields: Array<{
    name: string;
    id: string;
    widget: AuthRecoveryWidget;
    value: string;
    required: boolean;
    disabled: boolean;
    errors: string[];
    attrs: Record<string, string>;
  }>;
}

interface PasswordResetRequestApiPayload {
  form: AuthRecoveryFormApiPayload;
}

interface PasswordResetConfirmApiPayload {
  username: string;
  has_otp: boolean;
  form: AuthRecoveryFormApiPayload;
}

interface PasswordExpiredApiPayload {
  form: AuthRecoveryFormApiPayload;
}

interface OtpSyncApiPayload {
  form: AuthRecoveryFormApiPayload;
}

export interface PasswordResetRequestBootstrap {
  apiUrl: string;
  submitUrl: string;
  loginUrl: string;
  csrfToken?: string;
  initialPayload: PasswordResetRequestPayload | null;
}

export interface PasswordResetConfirmBootstrap {
  apiUrl: string;
  submitUrl: string;
  loginUrl: string;
  token: string;
  csrfToken?: string;
  initialPayload: PasswordResetConfirmPayload | null;
}

export interface PasswordExpiredBootstrap {
  apiUrl: string;
  submitUrl: string;
  loginUrl: string;
  csrfToken?: string;
  initialPayload: PasswordExpiredPayload | null;
}

export interface OtpSyncBootstrap {
  apiUrl: string;
  submitUrl: string;
  loginUrl: string;
  csrfToken?: string;
  initialPayload: OtpSyncPayload | null;
}

function normalizeFormPayload(payload: AuthRecoveryFormApiPayload): AuthRecoveryFormPayload {
  return {
    isBound: payload.is_bound,
    nonFieldErrors: payload.non_field_errors,
    fields: payload.fields.map((field) => ({
      name: field.name,
      id: field.id,
      widget: field.widget,
      value: field.value,
      required: field.required,
      disabled: field.disabled,
      errors: field.errors,
      attrs: field.attrs,
    })),
  };
}

function readInitialPayload<T>(root: HTMLElement, normalizer: (payload: T) => unknown): ReturnType<typeof normalizer> | null {
  const script = root.querySelector<HTMLScriptElement>("script[data-auth-recovery-initial-payload]");
  if (!script?.textContent) {
    return null;
  }
  return normalizer(JSON.parse(script.textContent) as T);
}

function normalizePasswordResetRequestPayload(payload: PasswordResetRequestApiPayload): PasswordResetRequestPayload {
  return { form: normalizeFormPayload(payload.form) };
}

function normalizePasswordResetConfirmPayload(payload: PasswordResetConfirmApiPayload): PasswordResetConfirmPayload {
  return {
    username: payload.username,
    hasOtp: payload.has_otp,
    form: normalizeFormPayload(payload.form),
  };
}

function normalizePasswordExpiredPayload(payload: PasswordExpiredApiPayload): PasswordExpiredPayload {
  return { form: normalizeFormPayload(payload.form) };
}

function normalizeOtpSyncPayload(payload: OtpSyncApiPayload): OtpSyncPayload {
  return { form: normalizeFormPayload(payload.form) };
}

async function fetchPayload<TApiPayload, TPayload>(apiUrl: string, errorMessage: string, normalizer: (payload: TApiPayload) => TPayload): Promise<TPayload> {
  const response = await fetch(apiUrl, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new Error(errorMessage);
  }
  return normalizer((await response.json()) as TApiPayload);
}

export function fetchPasswordResetRequestPayload(apiUrl: string): Promise<PasswordResetRequestPayload> {
  return fetchPayload(apiUrl, "Unable to load password reset form.", normalizePasswordResetRequestPayload);
}

export function fetchPasswordResetConfirmPayload(apiUrl: string): Promise<PasswordResetConfirmPayload> {
  return fetchPayload(apiUrl, "Unable to load password reset confirmation form.", normalizePasswordResetConfirmPayload);
}

export function fetchPasswordExpiredPayload(apiUrl: string): Promise<PasswordExpiredPayload> {
  return fetchPayload(apiUrl, "Unable to load password expired form.", normalizePasswordExpiredPayload);
}

export function fetchOtpSyncPayload(apiUrl: string): Promise<OtpSyncPayload> {
  return fetchPayload(apiUrl, "Unable to load OTP sync form.", normalizeOtpSyncPayload);
}

export function readPasswordResetRequestBootstrap(root: HTMLElement): PasswordResetRequestBootstrap | null {
  const apiUrl = String(root.dataset.authRecoveryPasswordResetApiUrl || "").trim();
  const submitUrl = String(root.dataset.authRecoveryPasswordResetSubmitUrl || "").trim();
  const loginUrl = String(root.dataset.authRecoveryPasswordResetLoginUrl || "").trim();
  const csrfToken = String(root.dataset.authRecoveryPasswordResetCsrfToken || "").trim();
  const initialPayload = readInitialPayload(root, normalizePasswordResetRequestPayload);
  if (!submitUrl || !loginUrl || (!apiUrl && initialPayload === null)) {
    return null;
  }
  return { apiUrl, submitUrl, loginUrl, csrfToken, initialPayload };
}

export function readPasswordResetConfirmBootstrap(root: HTMLElement): PasswordResetConfirmBootstrap | null {
  const apiUrl = String(root.dataset.authRecoveryPasswordResetConfirmApiUrl || "").trim();
  const submitUrl = String(root.dataset.authRecoveryPasswordResetConfirmSubmitUrl || "").trim();
  const loginUrl = String(root.dataset.authRecoveryPasswordResetConfirmLoginUrl || "").trim();
  const token = String(root.dataset.authRecoveryPasswordResetConfirmToken || "").trim();
  const csrfToken = String(root.dataset.authRecoveryPasswordResetConfirmCsrfToken || "").trim();
  const initialPayload = readInitialPayload(root, normalizePasswordResetConfirmPayload);
  if (!submitUrl || !loginUrl || !token || (!apiUrl && initialPayload === null)) {
    return null;
  }
  return { apiUrl, submitUrl, loginUrl, token, csrfToken, initialPayload };
}

export function readPasswordExpiredBootstrap(root: HTMLElement): PasswordExpiredBootstrap | null {
  const apiUrl = String(root.dataset.authRecoveryPasswordExpiredApiUrl || "").trim();
  const submitUrl = String(root.dataset.authRecoveryPasswordExpiredSubmitUrl || "").trim();
  const loginUrl = String(root.dataset.authRecoveryPasswordExpiredLoginUrl || "").trim();
  const csrfToken = String(root.dataset.authRecoveryPasswordExpiredCsrfToken || "").trim();
  const initialPayload = readInitialPayload(root, normalizePasswordExpiredPayload);
  if (!submitUrl || !loginUrl || (!apiUrl && initialPayload === null)) {
    return null;
  }
  return { apiUrl, submitUrl, loginUrl, csrfToken, initialPayload };
}

export function readOtpSyncBootstrap(root: HTMLElement): OtpSyncBootstrap | null {
  const apiUrl = String(root.dataset.authRecoveryOtpSyncApiUrl || "").trim();
  const submitUrl = String(root.dataset.authRecoveryOtpSyncSubmitUrl || "").trim();
  const loginUrl = String(root.dataset.authRecoveryOtpSyncLoginUrl || "").trim();
  const csrfToken = String(root.dataset.authRecoveryOtpSyncCsrfToken || "").trim();
  const initialPayload = readInitialPayload(root, normalizeOtpSyncPayload);
  if (!submitUrl || !loginUrl || (!apiUrl && initialPayload === null)) {
    return null;
  }
  return { apiUrl, submitUrl, loginUrl, csrfToken, initialPayload };
}