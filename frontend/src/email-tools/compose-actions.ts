/**
 * Centralized save / save-as logic for the ComposeCard email editor.
 *
 * Consumers call `useComposeSaveHandlers()` to wire up the document-level
 * custom events dispatched by `templated_email.js` (save-confirmed,
 * save-as-confirmed).  The composable handles CSRF, fetch, alerting,
 * and updating the live template `<select>`.
 */
import { onBeforeUnmount, type Ref } from "vue";

import { readCsrfToken } from "../shared/csrf";
import type { ComposeTemplateOption } from "./types";

// -- low-level API (also usable from non-Vue code like controller.ts) --

interface ComposeInstance {
  getTemplateId?: () => string;
  getValues?: () => Record<string, string>;
  getCsrfToken?: () => string;
  container?: HTMLElement;
}

/**
 * Save current compose content to an existing template.
 *
 * Returns `true` on success.
 */
export async function saveComposeTemplate(
  templateId: string,
  values: { subject: string; html_content: string; text_content: string },
  csrfToken?: string,
): Promise<boolean> {
  if (!templateId) {
    window.alert("Select a template to save, or use Save as.");
    return false;
  }

  const data = new FormData();
  data.append("email_template_id", templateId);
  data.append("subject", values.subject);
  data.append("html_content", values.html_content);
  data.append("text_content", values.text_content);

  try {
    const response = await fetch("/email-tools/templates/save/", {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken || readCsrfToken(), Accept: "application/json" },
      body: data,
    });
    const payload = (await response.json()) as { ok?: boolean };
    if (!response.ok || payload.ok !== true) {
      throw new Error("save failed");
    }
    window.alert("Template saved.");
    return true;
  } catch {
    window.alert("Failed to save template.");
    return false;
  }
}

export interface SaveAsResult {
  ok: boolean;
  id?: number;
  name?: string;
}

/**
 * Save current compose content as a new template.
 *
 * Returns the new template id/name on success, or `{ ok: false }`.
 */
export async function saveAsComposeTemplate(
  name: string,
  values: { subject: string; html_content: string; text_content: string },
  csrfToken?: string,
): Promise<SaveAsResult> {
  const trimmedName = name.trim();
  if (!trimmedName) {
    return { ok: false };
  }

  const data = new FormData();
  data.append("name", trimmedName);
  data.append("subject", values.subject);
  data.append("html_content", values.html_content);
  data.append("text_content", values.text_content);

  try {
    const response = await fetch("/email-tools/templates/save-as/", {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken || readCsrfToken(), Accept: "application/json" },
      body: data,
    });
    const payload = (await response.json()) as { ok?: boolean; id?: number; name?: string };
    if (!response.ok || payload.ok !== true) {
      throw new Error("save as failed");
    }
    return { ok: true, id: payload.id, name: payload.name ?? trimmedName };
  } catch {
    window.alert("Failed to create template.");
    return { ok: false };
  }
}

// -- helpers --

function extractComposeValues(instance: ComposeInstance | undefined): {
  subject: string;
  html_content: string;
  text_content: string;
} {
  const raw = instance?.getValues?.();
  return {
    subject: raw?.subject ?? "",
    html_content: raw?.html_content ?? raw?.html ?? "",
    text_content: raw?.text_content ?? raw?.text ?? "",
  };
}

function extractCsrfToken(instance: ComposeInstance | undefined): string {
  return instance?.getCsrfToken?.() ?? readCsrfToken();
}

/**
 * Add a template option to the live `<select>` element inside a compose
 * container so `templated_email.js` sees the new entry.
 */
function addOptionToLiveSelect(
  container: ParentNode | null | undefined,
  id: number | string,
  name: string,
): void {
  if (!container) return;
  const selectEl = container.querySelector<HTMLSelectElement>('select[name="email_template_id"]');
  if (!selectEl) return;
  const option = new Option(name, String(id), true, true);
  selectEl.append(option);
  selectEl.value = String(id);
}

// -- Vue composable --

export interface UseComposeSaveOptions {
  /** Reactive list of template options — updated in-place on save-as. */
  templateOptions: Ref<ComposeTemplateOption[]>;
  /** Reactive selected template id — updated on save-as. */
  selectedTemplateId: Ref<number | null>;
  /**
   * Optional container ref (e.g. modalRef) whose DOM contains the live
   * `<select>`.  Falls back to the compose instance's container.
   */
  containerRef?: Ref<HTMLElement | null>;
  /** Called after a successful save or save-as.  Optional. */
  onAfterSave?: () => void;
}

/**
 * Wire up `templated-email-compose:save-confirmed` and
 * `templated-email-compose:save-as-confirmed` document events to the
 * centralized save / save-as logic.
 *
 * Automatically removes listeners on unmount.
 */
export function useComposeSaveHandlers(options: UseComposeSaveOptions): void {
  async function handleSaveConfirmed(event: Event): Promise<void> {
    const detail = event instanceof CustomEvent ? (event.detail as { instance?: ComposeInstance }) : undefined;
    const instance = detail?.instance;
    const templateId = instance?.getTemplateId?.() ?? "";
    const values = extractComposeValues(instance);
    const token = extractCsrfToken(instance);

    const ok = await saveComposeTemplate(templateId, values, token);
    if (ok) {
      options.onAfterSave?.();
    }
  }

  async function handleSaveAsConfirmed(event: Event): Promise<void> {
    const detail = event instanceof CustomEvent
      ? (event.detail as { instance?: ComposeInstance; name?: string })
      : undefined;
    const name = String(detail?.name || "").trim();
    if (!name) return;

    const instance = detail?.instance;
    const values = extractComposeValues(instance);
    const token = extractCsrfToken(instance);

    const result = await saveAsComposeTemplate(name, values, token);
    if (!result.ok || result.id == null) return;

    // Update reactive state.
    options.templateOptions.value = [
      ...options.templateOptions.value,
      { id: result.id, name: result.name ?? name },
    ];
    options.selectedTemplateId.value = result.id;

    // Update the live <select> so templated_email.js sees the new entry.
    const container = options.containerRef?.value ?? instance?.container;
    addOptionToLiveSelect(container, result.id, result.name ?? name);

    window.alert("Template created.");
    options.onAfterSave?.();
  }

  document.addEventListener("templated-email-compose:save-confirmed", handleSaveConfirmed);
  document.addEventListener("templated-email-compose:save-as-confirmed", handleSaveAsConfirmed);

  onBeforeUnmount(() => {
    document.removeEventListener("templated-email-compose:save-confirmed", handleSaveConfirmed);
    document.removeEventListener("templated-email-compose:save-as-confirmed", handleSaveAsConfirmed);
  });
}
