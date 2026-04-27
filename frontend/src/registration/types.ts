type RegistrationWidget = "text" | "email" | "password" | "checkbox" | "hidden";

export interface RegistrationFormField {
  name: string;
  id: string;
  widget: RegistrationWidget;
  value: string;
  required: boolean;
  disabled: boolean;
  errors: string[];
  attrs: Record<string, string>;
  checked?: boolean;
}

export interface RegistrationFormPayload {
  isBound: boolean;
  nonFieldErrors: string[];
  fields: RegistrationFormField[];
}

export interface RegisterPagePayload {
  registrationOpen: boolean;
  form: RegistrationFormPayload;
}

export interface RegisterConfirmPayload {
  username: string;
  email: string | null;
  form: RegistrationFormPayload;
}

export interface RegisterActivatePayload {
  username: string;
  form: RegistrationFormPayload;
}

interface RegistrationFormApiPayload {
  is_bound: boolean;
  non_field_errors: string[];
  fields: Array<{
    name: string;
    id: string;
    widget: RegistrationWidget;
    value: string;
    required: boolean;
    disabled: boolean;
    errors: string[];
    attrs: Record<string, string>;
    checked?: boolean;
  }>;
}

interface RegisterPageApiPayload {
  registration_open: boolean;
  form: RegistrationFormApiPayload;
}

interface RegisterConfirmApiPayload {
  username: string;
  email: string | null;
  form: RegistrationFormApiPayload;
}

interface RegisterActivateApiPayload {
  username: string;
  form: RegistrationFormApiPayload;
}

export interface RegisterPageBootstrap {
  apiUrl: string;
  loginUrl: string;
  registerUrl: string;
  submitUrl: string;
  csrfToken?: string;
  initialPayload: RegisterPagePayload | null;
}

export interface RegisterConfirmBootstrap {
  apiUrl: string;
  submitUrl: string;
  loginUrl: string;
  csrfToken?: string;
  initialPayload: RegisterConfirmPayload | null;
}

export interface RegisterActivateBootstrap {
  apiUrl: string;
  submitUrl: string;
  startOverUrl: string;
  csrfToken?: string;
  initialPayload: RegisterActivatePayload | null;
}

function normalizeFormPayload(payload: RegistrationFormApiPayload): RegistrationFormPayload {
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
      checked: field.checked,
    })),
  };
}

function readInitialPayload<T>(root: HTMLElement, normalizer: (payload: T) => unknown): ReturnType<typeof normalizer> | null {
  const script = root.querySelector<HTMLScriptElement>("script[data-registration-initial-payload]");
  if (!script?.textContent) {
    return null;
  }
  return normalizer(JSON.parse(script.textContent) as T);
}

function normalizeRegisterPagePayload(payload: RegisterPageApiPayload): RegisterPagePayload {
  return {
    registrationOpen: payload.registration_open,
    form: normalizeFormPayload(payload.form),
  };
}

function normalizeRegisterConfirmPayload(payload: RegisterConfirmApiPayload): RegisterConfirmPayload {
  return {
    username: payload.username,
    email: payload.email,
    form: normalizeFormPayload(payload.form),
  };
}

function normalizeRegisterActivatePayload(payload: RegisterActivateApiPayload): RegisterActivatePayload {
  return {
    username: payload.username,
    form: normalizeFormPayload(payload.form),
  };
}

export async function fetchRegisterPagePayload(apiUrl: string): Promise<RegisterPagePayload> {
  const response = await fetch(apiUrl, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new Error("Unable to load registration page.");
  }
  return normalizeRegisterPagePayload((await response.json()) as RegisterPageApiPayload);
}

export async function fetchRegisterConfirmPayload(apiUrl: string): Promise<RegisterConfirmPayload> {
  const response = await fetch(apiUrl, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new Error("Unable to load email validation page.");
  }
  return normalizeRegisterConfirmPayload((await response.json()) as RegisterConfirmApiPayload);
}

export async function fetchRegisterActivatePayload(apiUrl: string): Promise<RegisterActivatePayload> {
  const response = await fetch(apiUrl, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new Error("Unable to load activation page.");
  }
  return normalizeRegisterActivatePayload((await response.json()) as RegisterActivateApiPayload);
}

export function readRegisterPageBootstrap(root: HTMLElement): RegisterPageBootstrap | null {
  const apiUrl = String(root.dataset.registerApiUrl || "").trim();
  const loginUrl = String(root.dataset.registerLoginUrl || "").trim();
  const registerUrl = String(root.dataset.registerRegisterUrl || "").trim() || String(root.dataset.registerSubmitUrl || "").trim();
  const submitUrl = String(root.dataset.registerSubmitUrl || "").trim();
  const csrfToken = String(root.dataset.registerCsrfToken || "").trim();
  const initialPayload = readInitialPayload(root, normalizeRegisterPagePayload);
  if (!loginUrl || !submitUrl || !registerUrl || (!apiUrl && initialPayload === null)) {
    return null;
  }
  return {
    apiUrl,
    loginUrl,
    registerUrl,
    submitUrl,
    csrfToken,
    initialPayload,
  };
}

export function readRegisterConfirmBootstrap(root: HTMLElement): RegisterConfirmBootstrap | null {
  const apiUrl = String(root.dataset.registerConfirmApiUrl || "").trim();
  const submitUrl = String(root.dataset.registerConfirmSubmitUrl || "").trim();
  const loginUrl = String(root.dataset.registerConfirmLoginUrl || "").trim();
  const csrfToken = String(root.dataset.registerConfirmCsrfToken || "").trim();
  const initialPayload = readInitialPayload(root, normalizeRegisterConfirmPayload);
  if (!submitUrl || !loginUrl || (!apiUrl && initialPayload === null)) {
    return null;
  }
  return {
    apiUrl,
    submitUrl,
    loginUrl,
    csrfToken,
    initialPayload,
  };
}

export function readRegisterActivateBootstrap(root: HTMLElement): RegisterActivateBootstrap | null {
  const apiUrl = String(root.dataset.registerActivateApiUrl || "").trim();
  const submitUrl = String(root.dataset.registerActivateSubmitUrl || "").trim();
  const startOverUrl = String(root.dataset.registerActivateStartOverUrl || "").trim();
  const csrfToken = String(root.dataset.registerActivateCsrfToken || "").trim();
  const initialPayload = readInitialPayload(root, normalizeRegisterActivatePayload);
  if (!submitUrl || !startOverUrl || (!apiUrl && initialPayload === null)) {
    return null;
  }
  return {
    apiUrl,
    submitUrl,
    startOverUrl,
    csrfToken,
    initialPayload,
  };
}