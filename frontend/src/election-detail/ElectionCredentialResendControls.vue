<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";

import ComposeCard from "../email-tools/ComposeCard.vue";
import { useComposeSaveHandlers } from "../email-tools/compose-actions";
import type { ComposeFieldSpec, ComposeTemplateOption, ComposeVariable } from "../email-tools/types";
import { readCsrfToken } from "../shared/csrf";
import type { ElectionCredentialResendBootstrap } from "./types";

declare global {
  interface Window {
    TemplatedEmailComposeRegistry?: {
      initAll?: (root?: ParentNode) => void;
      getAll?: () => unknown[];
    };
    TemplatedEmailComposePreview?: {
      schedulePreviewRefresh?: (compose: unknown, delayMs: number) => void;
    };
  }
}

type JQueryCollection = {
  select2?: (options?: unknown) => void;
  trigger?: (event: string) => void;
  val?: (value?: string | null) => unknown;
  on?: (event: string, callback: (...args: unknown[]) => void) => void;
};

type JQueryFunction = ((target: Element | string) => JQueryCollection) & {
  fn?: {
    select2?: unknown;
  };
};

interface TemplatePayload {
  subject: string;
  html_content: string;
  text_content: string;
  variables: { name: string; example: string }[];
  template_options: { id: number; name: string }[];
  selected_template_id: number | null;
}

const props = defineProps<{
  bootstrap: ElectionCredentialResendBootstrap;
}>();

const selectRef = ref<HTMLSelectElement | null>(null);
const modalRef = ref<HTMLElement | null>(null);
const username = ref("");
const select2Initialized = ref(false);

// Modal state
const modalMode = ref<"single" | "all" | null>(null);
const isLoadingTemplate = ref(false);
const templateLoadError = ref("");
const isSubmitting = ref(false);
const submitError = ref("");
const successMessage = ref("");

// ComposeCard field state
const subjectValue = ref("");
const htmlContentValue = ref("");
const textContentValue = ref("");
const availableVariables = ref<ComposeVariable[]>([]);
const templateOptions = ref<ComposeTemplateOption[]>([]);
const selectedTemplateId = ref<number | null>(null);

const subjectField = computed<ComposeFieldSpec>(() => ({
  id: "credential-email-subject",
  name: "subject",
  value: subjectValue.value,
  cssClass: "form-control",
  attrs: {},
  errors: [],
  disabled: isSubmitting.value,
}));

const htmlContentField = computed<ComposeFieldSpec>(() => ({
  id: "credential-email-html",
  name: "html_content",
  value: htmlContentValue.value,
  cssClass: "form-control",
  rows: 12,
  attrs: {},
  errors: [],
  disabled: isSubmitting.value,
}));

const textContentField = computed<ComposeFieldSpec>(() => ({
  id: "credential-email-text",
  name: "text_content",
  value: textContentValue.value,
  cssClass: "form-control",
  rows: 12,
  attrs: {},
  errors: [],
  disabled: isSubmitting.value,
}));

const emptyPreview = { subject: "", html: "", text: "" };

const isOpen = computed(() => props.bootstrap.electionStatus === "open");

const recipientCount = computed(() => {
  if (modalMode.value === "single") return 1;
  return props.bootstrap.eligibleUsernames.length;
});

const sendButtonLabel = computed(() => {
  const n = recipientCount.value;
  return `Send ${n} email${n === 1 ? "" : "s"}`;
});

const singleButtonLabel = computed(() => isOpen.value ? "Send reminder" : "Send email");
const allButtonLabel = computed(() => isOpen.value ? "Send reminder to all" : "Send email to all");

const recipientLabel = computed(() => {
  if (modalMode.value === "single") {
    return username.value;
  }
  const count = props.bootstrap.eligibleUsernames.length;
  return `all ${count} eligible voter${count === 1 ? "" : "s"}`;
});

/**
 * Read current field values from the DOM, since ComposeCard binds
 * one-way (:value) and CodeMirror may have replaced the textareas.
 */
function readFieldValues(): { subject: string; html: string; text: string } {
  const root = modalRef.value;
  if (!root) {
    return { subject: subjectValue.value, html: htmlContentValue.value, text: textContentValue.value };
  }

  const subjectEl = root.querySelector<HTMLInputElement>('[name="subject"]');
  const htmlEl = root.querySelector<HTMLTextAreaElement>('[name="html_content"]');
  const textEl = root.querySelector<HTMLTextAreaElement>('[name="text_content"]');

  // CodeMirror stores values on the element as .CodeMirror.getValue()
  const cmHtml = (htmlEl as HTMLTextAreaElement & { CodeMirror?: { getValue: () => string } } | null)?.CodeMirror;
  const cmText = (textEl as HTMLTextAreaElement & { CodeMirror?: { getValue: () => string } } | null)?.CodeMirror;

  return {
    subject: subjectEl?.value ?? subjectValue.value,
    html: cmHtml ? cmHtml.getValue() : (htmlEl?.value ?? htmlContentValue.value),
    text: cmText ? cmText.getValue() : (textEl?.value ?? textContentValue.value),
  };
}

// The user whose info is shown in variable examples and the preview.
// Single-user: the selected user. Send-all: the first eligible user.
const previewUsername = computed(() => {
  if (modalMode.value === "single" && username.value) {
    return username.value;
  }
  return props.bootstrap.eligibleUsernames[0] ?? "";
});

const previewUrl = computed(() => {
  const base = props.bootstrap.credentialEmailPreviewUrl;
  if (!previewUsername.value) return base;
  const sep = base.includes("?") ? "&" : "?";
  return `${base}${sep}preview_username=${encodeURIComponent(previewUsername.value)}`;
});

async function openModal(mode: "single" | "all"): Promise<void> {
  modalMode.value = mode;
  isLoadingTemplate.value = true;
  templateLoadError.value = "";
  submitError.value = "";
  successMessage.value = "";

  try {
    let apiUrl = props.bootstrap.credentialEmailTemplateApiUrl;
    const targetUsername = mode === "single" ? username.value : (props.bootstrap.eligibleUsernames[0] ?? "");
    if (targetUsername) {
      const sep = apiUrl.includes("?") ? "&" : "?";
      apiUrl = `${apiUrl}${sep}preview_username=${encodeURIComponent(targetUsername)}`;
    }

    const response = await fetch(apiUrl, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });

    if (!response.ok) {
      templateLoadError.value = "Unable to load the email template right now.";
      return;
    }

    const payload = (await response.json()) as TemplatePayload;
    subjectValue.value = payload.subject || "";
    htmlContentValue.value = payload.html_content || "";
    textContentValue.value = payload.text_content || "";
    availableVariables.value = payload.variables || [];
    templateOptions.value = (payload.template_options || []).map((t) => ({ id: t.id, name: t.name }));
    selectedTemplateId.value = payload.selected_template_id ?? null;

    // Switch from "Loading..." to the ComposeCard before nextTick so the
    // compose container is in the DOM when initAll scans for it.
    isLoadingTemplate.value = false;

    // Let Vue render ComposeCard, then init CodeMirror + preview refresh.
    await nextTick();
    window.TemplatedEmailComposeRegistry?.initAll?.(modalRef.value ?? document);

    // Trigger an initial preview render now that CodeMirror is ready.
    const instances = window.TemplatedEmailComposeRegistry?.getAll?.() ?? [];
    const lastInstance = instances[instances.length - 1];
    if (lastInstance) {
      window.TemplatedEmailComposePreview?.schedulePreviewRefresh?.(lastInstance, 0);
    }
  } catch {
    templateLoadError.value = "Unable to load the email template right now.";
    isLoadingTemplate.value = false;
  }
}

function closeModal(): void {
  modalMode.value = null;
}

// Wire up save / save-as event handlers via the centralized composable.
useComposeSaveHandlers({
  templateOptions,
  selectedTemplateId,
  containerRef: modalRef,
});

async function sendCredentials(): Promise<void> {
  if (isSubmitting.value) {
    return;
  }

  isSubmitting.value = true;
  submitError.value = "";

  const targetUsername = modalMode.value === "single" ? username.value : "";
  const fields = readFieldValues();

  try {
    const response = await fetch(props.bootstrap.sendMailCredentialsApiUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": readCsrfToken(),
      },
      body: JSON.stringify({
        username: targetUsername,
        subject_template: fields.subject,
        html_template: fields.html,
        text_template: fields.text,
      }),
    });

    const payload = (await response.json().catch(() => ({
      errors: ["Unable to send the credential emails right now."],
    }))) as {
      errors?: string[];
      message?: string;
      ok?: boolean;
    };

    if (!response.ok || payload.ok !== true || !payload.message) {
      submitError.value = payload.errors?.[0] || "Unable to send the credential emails right now.";
      return;
    }

    successMessage.value = payload.message;
    modalMode.value = null;
  } catch {
    submitError.value = "Unable to send the credential emails right now.";
  } finally {
    isSubmitting.value = false;
  }
}

// --- Select2 integration ---

function getJQuery(): JQueryFunction | null {
  const maybeJQuery = (window as typeof window & { jQuery?: JQueryFunction; $?: JQueryFunction }).jQuery
    || (window as typeof window & { jQuery?: JQueryFunction; $?: JQueryFunction }).$;
  return maybeJQuery || null;
}

function supportsSelect2(jquery: JQueryFunction | null): jquery is JQueryFunction {
  return Boolean(jquery?.fn && typeof jquery.fn.select2 === "function");
}

function initSelect2(): void {
  const el = selectRef.value;
  if (!el || select2Initialized.value) {
    return;
  }

  const jquery = getJQuery();
  if (!supportsSelect2(jquery)) {
    return;
  }

  select2Initialized.value = true;

  const $el = jquery(el);
  $el.select2?.({
    width: "100%",
    allowClear: true,
    placeholder: "Select an eligible voter",
  });

  $el.on?.("change select2:select select2:unselect select2:clear", () => {
    const val = $el.val?.();
    username.value = typeof val === "string" ? val : "";
  });
}

function onSelectionChange(event: Event): void {
  const target = event.target as HTMLSelectElement | null;
  username.value = target?.value || "";
}

watch(
  () => props.bootstrap.eligibleUsernames,
  async (usernames) => {
    if (usernames.length > 0 && !select2Initialized.value) {
      await nextTick();
      initSelect2();
    }
  },
  { flush: "post", immediate: true },
);
</script>

<template>
  <div data-election-credential-resend-vue-root>
    <div v-if="successMessage" class="alert alert-success" role="status">
      <p class="mb-0">{{ successMessage }}</p>
    </div>

    <div class="d-flex align-items-center" style="gap: .5rem;">
      <div class="d-flex align-items-center" style="gap: .5rem; min-width: 0; flex: 1 1 auto;">
        <label class="sr-only" for="resend-credential-username">Username</label>
        <div style="min-width: 0; flex: 1 1 auto;">
          <select
            id="resend-credential-username"
            ref="selectRef"
            name="username"
            class="form-control"
            @change="onSelectionChange"
          >
            <option value=""></option>
            <option v-for="eligibleUsername in bootstrap.eligibleUsernames" :key="eligibleUsername" :value="eligibleUsername">
              {{ eligibleUsername }}
            </option>
          </select>
        </div>
        <button
          data-testid="send-reminder-single"
          type="button"
          class="btn btn-outline-primary btn-sm"
          :disabled="!username"
          :title="isOpen ? 'Send a credential reminder to the selected user' : 'Send an email to the selected user'"
          style="white-space: nowrap;"
          @click="openModal('single')"
        >
          {{ singleButtonLabel }}
        </button>
      </div>

      <div class="ml-auto flex-shrink-0">
        <button
          data-testid="send-reminder-all"
          type="button"
          class="btn btn-outline-primary btn-sm"
          :disabled="bootstrap.eligibleUsernames.length === 0"
          :title="isOpen ? 'Send credential reminders to all eligible voters' : 'Send an email to all eligible voters'"
          style="white-space: nowrap;"
          @click="openModal('all')"
        >
          {{ allButtonLabel }}
        </button>
      </div>
    </div>

    <!-- Compose + confirm modal -->
    <div
      v-if="modalMode"
      class="modal d-block"
      tabindex="-1"
      role="dialog"
      style="background: rgba(0, 0, 0, 0.5);"
      @click.self="closeModal"
    >
      <div class="modal-dialog modal-xl" role="document" style="max-height: calc(100vh - 3.5rem); display: flex; flex-direction: column;">
        <div class="modal-content" style="max-height: inherit; display: flex; flex-direction: column;">
          <div class="modal-header flex-shrink-0">
            <h5 class="modal-title">{{ isOpen ? 'Send credential reminder to' : 'Send email to' }} {{ recipientLabel }}</h5>
            <button type="button" class="close" aria-label="Close" @click="closeModal">
              <span aria-hidden="true">&times;</span>
            </button>
          </div>
          <div ref="modalRef" class="modal-body" style="overflow-y: auto; flex: 1 1 auto;">
            <div v-if="isLoadingTemplate" class="text-muted">Loading template...</div>
            <div v-else-if="templateLoadError" class="alert alert-danger mb-0">{{ templateLoadError }}</div>
            <template v-else>
              <div v-if="submitError" class="alert alert-danger">{{ submitError }}</div>

              <p class="text-muted small mb-2">
                {{ isOpen
                  ? 'Edit the email content below. Each recipient will receive their own personalized email with their unique voting credential and link.'
                  : 'Edit the email content below. Each recipient will receive their own personalized email.'
                }}
              </p>

              <ComposeCard
                title="Credential email"
                :help-text="isOpen ? 'Customize the credential reminder email.' : 'Customize the email content.'"
                :show-card="false"
                :show-save-buttons="true"
                :template-options="templateOptions"
                :selected-template-id="selectedTemplateId"
                :available-variables="availableVariables"
                :preview-url="previewUrl"
                :skip-initial-preview-refresh="false"
                :preview="emptyPreview"
                :subject-field="subjectField"
                :html-content-field="htmlContentField"
                :text-content-field="textContentField"
              >
                <template #hidden-fields>
                  <input
                    v-if="previewUsername"
                    type="hidden"
                    name="preview_username"
                    data-compose-extra-field
                    :value="previewUsername"
                  >
                </template>
              </ComposeCard>
            </template>
          </div>
          <div v-if="!isLoadingTemplate && !templateLoadError" class="modal-footer flex-shrink-0 d-flex justify-content-between">
            <button data-testid="send-credentials-cancel" type="button" class="btn btn-secondary" :disabled="isSubmitting" @click="closeModal">Cancel</button>
            <button
              data-testid="send-credentials-confirm"
              type="button"
              class="btn btn-danger"
              :disabled="isSubmitting"
              @click="sendCredentials"
            >
              <span v-if="isSubmitting">Sending...</span>
              <span v-else>{{ sendButtonLabel }}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>