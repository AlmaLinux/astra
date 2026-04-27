export interface EmailTemplateListItem {
  id: number;
  name: string;
  description: string;
  isLocked: boolean;
}

export interface EmailTemplatesPayload {
  templates: EmailTemplateListItem[];
}

interface EmailTemplatesApiPayload {
  templates: Array<{
    id: number;
    name: string;
    description: string;
    is_locked: boolean;
  }>;
}

export interface EmailTemplatesBootstrap {
  apiUrl: string;
  createUrl: string;
  editUrlTemplate: string;
  deleteUrlTemplate: string;
  csrfToken: string;
  initialPayload: EmailTemplatesPayload | null;
}

export interface EmailTemplateEditorField {
  name: string;
  id: string;
  widget: "text" | "textarea" | "select";
  value: string;
  required: boolean;
  disabled: boolean;
  errors: string[];
  attrs: Record<string, string>;
}

export interface EmailTemplateEditorForm {
  isBound: boolean;
  nonFieldErrors: string[];
  fields: EmailTemplateEditorField[];
}

export interface EmailTemplateEditorTemplateOption {
  id: number;
  name: string;
}

export interface EmailTemplateEditorVariable {
  name: string;
  example: string;
}

export interface EmailTemplateEditorPayload {
  mode: "create" | "edit";
  template: {
    id: number;
    name: string;
    description: string;
    isLocked: boolean;
  } | null;
  form: EmailTemplateEditorForm;
  compose: {
    selectedTemplateId: number | null;
    templateOptions: EmailTemplateEditorTemplateOption[];
    availableVariables: EmailTemplateEditorVariable[];
    preview: {
      subject: string;
      html: string;
      text: string;
    };
  };
}

interface EmailTemplateEditorApiPayload {
  mode: "create" | "edit";
  template: {
    id: number;
    name: string;
    description: string;
    is_locked: boolean;
  } | null;
  form: {
    is_bound: boolean;
    non_field_errors: string[];
    fields: Array<{
      name: string;
      id: string;
      widget: "text" | "textarea" | "select";
      value: string;
      required: boolean;
      disabled: boolean;
      errors: string[];
      attrs: Record<string, string>;
    }>;
  };
  compose: {
    selected_template_id: number | null;
    template_options: EmailTemplateEditorTemplateOption[];
    available_variables: EmailTemplateEditorVariable[];
    preview: {
      subject: string;
      html: string;
      text: string;
    };
  };
}

export interface EmailTemplateEditorBootstrap {
  apiUrl: string;
  listUrl: string;
  submitUrl: string;
  previewUrl: string;
  deleteUrl: string | null;
  csrfToken: string;
  initialPayload: EmailTemplateEditorPayload | null;
}

export interface MailImageItem {
  key: string;
  relativeKey: string;
  url: string;
  sizeBytes: number;
  modifiedAt: string;
}

export interface MailImagesPayload {
  mailImagesPrefix: string;
  exampleImageUrl: string;
  images: MailImageItem[];
}

interface MailImagesApiPayload {
  mail_images_prefix: string;
  example_image_url: string;
  images: Array<{
    key: string;
    relative_key: string;
    url: string;
    size_bytes: number;
    modified_at: string;
  }>;
}

export interface MailImagesBootstrap {
  apiUrl: string;
  submitUrl: string;
  csrfToken: string;
  initialPayload: MailImagesPayload | null;
}

export interface SendMailFieldOption {
  value: string;
  label: string;
}

export interface SendMailField {
  name: string;
  id: string;
  widget: "hidden" | "text" | "textarea" | "select" | "select-multiple" | "file";
  value: string | string[];
  required: boolean;
  disabled: boolean;
  errors: string[];
  attrs: Record<string, string>;
  options?: SendMailFieldOption[];
}

export interface SendMailForm {
  isBound: boolean;
  nonFieldErrors: string[];
  fields: SendMailField[];
}

export interface SendMailTemplateOption {
  id: number;
  name: string;
}

export interface SendMailRecipientVariable {
  name: string;
  example: string;
}

export interface SendMailPayload {
  selectedRecipientMode: string;
  actionStatus: string;
  hasSavedCsvRecipients: boolean;
  createdTemplateId: number | null;
  templates: SendMailTemplateOption[];
  form: SendMailForm;
  recipientPreview: {
    variables: SendMailRecipientVariable[];
    recipientCount: number;
    firstContext: Record<string, string>;
    skippedCount: number;
  };
  compose: {
    selectedTemplateId: number | null;
    preview: {
      subject: string;
      html: string;
      text: string;
    };
  };
}

interface SendMailApiPayload {
  selected_recipient_mode: string;
  action_status: string;
  has_saved_csv_recipients: boolean;
  created_template_id: number | null;
  templates: SendMailTemplateOption[];
  form: {
    is_bound: boolean;
    non_field_errors: string[];
    fields: Array<{
      name: string;
      id: string;
      widget: "hidden" | "text" | "textarea" | "select" | "select-multiple" | "file";
      value: string | string[];
      required: boolean;
      disabled: boolean;
      errors: string[];
      attrs: Record<string, string>;
      options?: SendMailFieldOption[];
    }>;
  };
  recipient_preview: {
    variables: SendMailRecipientVariable[];
    recipient_count: number;
    first_context: Record<string, string>;
    skipped_count: number;
  };
  compose: {
    selected_template_id: number | null;
    preview: {
      subject: string;
      html: string;
      text: string;
    };
  };
}

export interface SendMailBootstrap {
  apiUrl: string;
  submitUrl: string;
  previewUrl: string;
  csrfToken: string;
  initialPayload: SendMailPayload | null;
}

function parseJsonScript<T>(root: HTMLElement, selector: string): T | null {
  const script = root.querySelector<HTMLScriptElement>(selector);
  if (!script?.textContent) {
    return null;
  }

  return JSON.parse(script.textContent) as T;
}

function normalizeEmailTemplatesPayload(payload: EmailTemplatesApiPayload): EmailTemplatesPayload {
  return {
    templates: payload.templates.map((template) => ({
      id: template.id,
      name: template.name,
      description: template.description,
      isLocked: template.is_locked,
    })),
  };
}

function normalizeEmailTemplateEditorPayload(payload: EmailTemplateEditorApiPayload): EmailTemplateEditorPayload {
  return {
    mode: payload.mode,
    template: payload.template === null ? null : {
      id: payload.template.id,
      name: payload.template.name,
      description: payload.template.description,
      isLocked: payload.template.is_locked,
    },
    form: {
      isBound: payload.form.is_bound,
      nonFieldErrors: payload.form.non_field_errors,
      fields: payload.form.fields.map((field) => ({
        name: field.name,
        id: field.id,
        widget: field.widget,
        value: field.value,
        required: field.required,
        disabled: field.disabled,
        errors: field.errors,
        attrs: field.attrs,
      })),
    },
    compose: {
      selectedTemplateId: payload.compose.selected_template_id,
      templateOptions: payload.compose.template_options,
      availableVariables: payload.compose.available_variables,
      preview: payload.compose.preview,
    },
  };
}

function normalizeMailImagesPayload(payload: MailImagesApiPayload): MailImagesPayload {
  return {
    mailImagesPrefix: payload.mail_images_prefix,
    exampleImageUrl: payload.example_image_url,
    images: payload.images.map((image) => ({
      key: image.key,
      relativeKey: image.relative_key,
      url: image.url,
      sizeBytes: image.size_bytes,
      modifiedAt: image.modified_at,
    })),
  };
}

function normalizeSendMailField(field: SendMailApiPayload["form"]["fields"][number]): SendMailField {
  return {
    name: field.name,
    id: field.id,
    widget: field.widget,
    value: field.value,
    required: field.required,
    disabled: field.disabled,
    errors: field.errors,
    attrs: field.attrs,
    options: field.options,
  };
}

function normalizeSendMailPayload(payload: SendMailApiPayload): SendMailPayload {
  return {
    selectedRecipientMode: payload.selected_recipient_mode,
    actionStatus: payload.action_status,
    hasSavedCsvRecipients: payload.has_saved_csv_recipients,
    createdTemplateId: payload.created_template_id,
    templates: payload.templates,
    form: {
      isBound: payload.form.is_bound,
      nonFieldErrors: payload.form.non_field_errors,
      fields: payload.form.fields.map(normalizeSendMailField),
    },
    recipientPreview: {
      variables: payload.recipient_preview.variables,
      recipientCount: payload.recipient_preview.recipient_count,
      firstContext: payload.recipient_preview.first_context,
      skippedCount: payload.recipient_preview.skipped_count,
    },
    compose: {
      selectedTemplateId: payload.compose.selected_template_id,
      preview: payload.compose.preview,
    },
  };
}

async function fetchJson<T>(apiUrl: string): Promise<T> {
  const response = await fetch(apiUrl, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });

  if (!response.ok) {
    throw new Error(`Unable to load ${apiUrl}`);
  }

  return await response.json() as T;
}

export async function fetchEmailTemplatesPayload(apiUrl: string): Promise<EmailTemplatesPayload> {
  return normalizeEmailTemplatesPayload(await fetchJson<EmailTemplatesApiPayload>(apiUrl));
}

export async function fetchEmailTemplateEditorPayload(apiUrl: string): Promise<EmailTemplateEditorPayload> {
  return normalizeEmailTemplateEditorPayload(await fetchJson<EmailTemplateEditorApiPayload>(apiUrl));
}

export async function fetchMailImagesPayload(apiUrl: string): Promise<MailImagesPayload> {
  return normalizeMailImagesPayload(await fetchJson<MailImagesApiPayload>(apiUrl));
}

export async function fetchSendMailPayload(apiUrl: string): Promise<SendMailPayload> {
  return normalizeSendMailPayload(await fetchJson<SendMailApiPayload>(apiUrl));
}

export function readEmailTemplatesBootstrap(root: HTMLElement): EmailTemplatesBootstrap | null {
  const apiUrl = String(root.dataset.emailTemplatesApiUrl || "").trim();
  const createUrl = String(root.dataset.emailTemplateCreateUrl || "").trim();
  const editUrlTemplate = String(root.dataset.emailTemplateEditUrlTemplate || "").trim();
  const deleteUrlTemplate = String(root.dataset.emailTemplateDeleteUrlTemplate || "").trim();
  const csrfToken = String(root.dataset.emailTemplateCsrfToken || "").trim();
  const initialPayloadRaw = parseJsonScript<EmailTemplatesApiPayload>(root, "#email-templates-initial-payload");
  const initialPayload = initialPayloadRaw === null ? null : normalizeEmailTemplatesPayload(initialPayloadRaw);

  if (!createUrl || !editUrlTemplate || !deleteUrlTemplate || (!apiUrl && initialPayload === null)) {
    return null;
  }

  return {
    apiUrl,
    createUrl,
    editUrlTemplate,
    deleteUrlTemplate,
    csrfToken,
    initialPayload,
  };
}

export function readEmailTemplateEditorBootstrap(root: HTMLElement): EmailTemplateEditorBootstrap | null {
  const apiUrl = String(root.dataset.emailTemplateEditorApiUrl || "").trim();
  const listUrl = String(root.dataset.emailTemplateListUrl || "").trim();
  const submitUrl = String(root.dataset.emailTemplateSubmitUrl || "").trim();
  const previewUrl = String(root.dataset.emailTemplatePreviewUrl || "").trim();
  const deleteUrlRaw = String(root.dataset.emailTemplateDeleteUrl || "").trim();
  const csrfToken = String(root.dataset.emailTemplateCsrfToken || "").trim();
  const initialPayloadRaw = parseJsonScript<EmailTemplateEditorApiPayload>(root, "#email-template-editor-initial-payload");
  const initialPayload = initialPayloadRaw === null ? null : normalizeEmailTemplateEditorPayload(initialPayloadRaw);

  if (!listUrl || !submitUrl || !previewUrl || (!apiUrl && initialPayload === null)) {
    return null;
  }

  return {
    apiUrl,
    listUrl,
    submitUrl,
    previewUrl,
    deleteUrl: deleteUrlRaw || null,
    csrfToken,
    initialPayload,
  };
}

export function readMailImagesBootstrap(root: HTMLElement): MailImagesBootstrap | null {
  const apiUrl = String(root.dataset.mailImagesApiUrl || "").trim();
  const submitUrl = String(root.dataset.mailImagesSubmitUrl || "").trim();
  const csrfToken = String(root.dataset.mailImagesCsrfToken || "").trim();
  const initialPayloadRaw = parseJsonScript<MailImagesApiPayload>(root, "#mail-images-initial-payload");
  const initialPayload = initialPayloadRaw === null ? null : normalizeMailImagesPayload(initialPayloadRaw);

  if (!submitUrl || (!apiUrl && initialPayload === null)) {
    return null;
  }

  return {
    apiUrl,
    submitUrl,
    csrfToken,
    initialPayload,
  };
}

export function readSendMailBootstrap(root: HTMLElement): SendMailBootstrap | null {
  const apiUrl = String(root.dataset.sendMailApiUrl || "").trim();
  const submitUrl = String(root.dataset.sendMailSubmitUrl || "").trim();
  const previewUrl = String(root.dataset.sendMailPreviewUrl || "").trim();
  const csrfToken = String(root.dataset.sendMailCsrfToken || "").trim();
  const initialPayloadRaw = parseJsonScript<SendMailApiPayload>(root, "#send-mail-initial-payload");
  const initialPayload = initialPayloadRaw === null ? null : normalizeSendMailPayload(initialPayloadRaw);

  if (!submitUrl || !previewUrl || (!apiUrl && initialPayload === null)) {
    return null;
  }

  return {
    apiUrl,
    submitUrl,
    previewUrl,
    csrfToken,
    initialPayload,
  };
}

export function replaceTemplateToken(urlTemplate: string, value: number | string): string {
  return urlTemplate.replace("__template_id__", String(value));
}
