<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";

import ComposeCard from "./ComposeCard.vue";
import { fetchEmailTemplateEditorPayload, toComposeFieldSpec, type EmailTemplateEditorBootstrap, type EmailTemplateEditorField, type EmailTemplateEditorPayload } from "./types";

declare global {
  interface Window {
    TemplatedEmailComposeRegistry?: {
      initAll?: (root?: ParentNode) => void;
      getAll?: () => unknown[];
    };
  }
}

const props = defineProps<{
  bootstrap: EmailTemplateEditorBootstrap;
}>();

const payload = ref<EmailTemplateEditorPayload | null>(props.bootstrap.initialPayload);
const loadError = ref("");

const fieldLabels: Record<string, string> = {
  name: "Name",
  description: "Description",
};

const fieldByName = computed<Record<string, EmailTemplateEditorField>>(() => {
  const fields = payload.value?.form.fields || [];
  return Object.fromEntries(fields.map((field) => [field.name, field]));
});

function templateField(name: string): EmailTemplateEditorField | null {
  return fieldByName.value[name] || null;
}

function fieldClass(field: EmailTemplateEditorField | null): string {
  return field?.attrs.class || "form-control";
}

function fieldAttrs(field: EmailTemplateEditorField | null): Record<string, string> {
  if (field === null) {
    return {};
  }

  const attrs = { ...field.attrs };
  delete attrs.class;
  delete attrs.rows;
  return attrs;
}

const subjectField = computed(() => toComposeFieldSpec(templateField("subject")));
const htmlContentField = computed(() => toComposeFieldSpec(templateField("html_content")));
const textContentField = computed(() => toComposeFieldSpec(templateField("text_content")));
const composePreview = computed(() => payload.value?.compose.preview ?? { subject: "", html: "", text: "" });

async function loadPayload(): Promise<void> {
  if (payload.value !== null || !props.bootstrap.apiUrl) {
    return;
  }

  try {
    payload.value = await fetchEmailTemplateEditorPayload(props.bootstrap.apiUrl);
  } catch {
    loadError.value = "Unable to load the template editor right now.";
  }
}

function initCompose(): void {
  window.TemplatedEmailComposeRegistry?.initAll?.(document);
}

watch(
  () => payload.value,
  async (value) => {
    if (value === null) {
      return;
    }

    await nextTick();
    initCompose();
  },
  { immediate: true },
);

onMounted(async () => {
  await loadPayload();
});
</script>

<template>
  <div data-email-template-editor-vue-root>
    <div v-if="loadError" class="alert alert-danger" role="alert">{{ loadError }}</div>
    <div v-else-if="!payload" class="text-muted">Loading template editor...</div>
    <form v-else :action="bootstrap.submitUrl" method="post">
      <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">

      <div v-if="payload.form.nonFieldErrors.length" class="alert alert-danger" role="alert">
        <div v-for="errorItem in payload.form.nonFieldErrors" :key="errorItem">{{ errorItem }}</div>
      </div>

      <div class="row">
        <div class="col-lg-12">
          <div class="card card-outline card-secondary">
            <div class="card-header">
              <h3 class="card-title">Template details</h3>
            </div>
            <div class="card-body">
              <div v-for="fieldName in ['name', 'description']" :key="fieldName" class="form-group" :class="fieldName === 'description' ? 'mb-0' : ''">
                <label v-if="templateField(fieldName)" :for="templateField(fieldName)?.id">{{ fieldLabels[fieldName] }}:</label>
                <input
                  v-if="templateField(fieldName)"
                  :id="templateField(fieldName)?.id"
                  v-model="templateField(fieldName)!.value"
                  :name="fieldName"
                  type="text"
                  :class="fieldClass(templateField(fieldName))"
                  :disabled="templateField(fieldName)?.disabled"
                  :required="templateField(fieldName)?.required"
                  v-bind="fieldAttrs(templateField(fieldName))"
                >
                <div v-for="fieldError in templateField(fieldName)?.errors || []" :key="fieldError" class="invalid-feedback d-block">{{ fieldError }}</div>
              </div>
              <div v-if="payload.template?.isLocked" class="text-muted small mt-1">This template is referenced by the app configuration and cannot be renamed.</div>
            </div>
          </div>
        </div>
      </div>

      <ComposeCard
        :template-options="payload.compose.templateOptions"
        :selected-template-id="payload.compose.selectedTemplateId"
        :template-selector-disabled="payload.mode === 'edit'"
        :available-variables="payload.compose.availableVariables"
        :preview-url="bootstrap.previewUrl"
        :skip-initial-preview-refresh="true"
        :preview="composePreview"
        :subject-field="subjectField"
        :html-content-field="htmlContentField"
        :text-content-field="textContentField"
      />

      <div class="row">
        <div class="col-lg-12">
          <div class="card card-outline card-primary">
            <div class="card-body d-flex align-items-center flex-wrap" style="gap: .75rem;">
              <a class="btn btn-outline-secondary" :href="bootstrap.listUrl" title="Back to the template list">Back</a>

              <div class="flex-grow-1 text-center">
                <button
                  v-if="payload.template && !payload.template.isLocked && bootstrap.deleteUrl"
                  type="button"
                  class="btn btn-outline-danger"
                  data-toggle="modal"
                  data-target="#email-template-delete-modal"
                  title="Delete this template"
                >
                  Delete
                </button>
                <button
                  v-else-if="payload.template && payload.template.isLocked"
                  type="button"
                  class="btn btn-outline-danger"
                  disabled
                  aria-disabled="true"
                  title="This template is referenced by the app configuration and cannot be deleted."
                >
                  Delete
                </button>
              </div>

              <button type="submit" class="btn btn-success ml-auto" title="Save template changes">Save</button>
            </div>
          </div>
        </div>
      </div>

      <div v-if="payload.template && !payload.template.isLocked && bootstrap.deleteUrl" class="modal fade" id="email-template-delete-modal" tabindex="-1" role="dialog" aria-hidden="true" aria-labelledby="email-template-delete-modal-title">
        <div class="modal-dialog" role="document">
          <div class="modal-content">
            <div class="modal-header">
              <h5 id="email-template-delete-modal-title" class="modal-title">Delete email template?</h5>
              <button type="button" class="close" data-dismiss="modal" aria-label="Close" title="Close dialog">
                <span aria-hidden="true">&times;</span>
              </button>
            </div>
            <div class="modal-body">
              <div class="mb-3">Delete template: <strong>{{ payload.template.name }}</strong><p>This cannot be undone.</p></div>
              <form method="post" :action="bootstrap.deleteUrl" class="m-0">
                <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
                <div class="d-flex justify-content-between">
                  <button type="button" class="btn btn-secondary" data-dismiss="modal" aria-label="Cancel" title="Close dialog without applying changes">Cancel</button>
                  <button type="submit" class="btn btn-danger" title="Confirm and apply this action">Delete</button>
                </div>
              </form>
            </div>
          </div>
        </div>
      </div>
    </form>
  </div>
</template>
