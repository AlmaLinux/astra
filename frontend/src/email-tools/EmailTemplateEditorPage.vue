<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";

import { fetchEmailTemplateEditorPayload, type EmailTemplateEditorBootstrap, type EmailTemplateEditorField, type EmailTemplateEditorPayload } from "./types";

declare global {
  interface Window {
    TemplatedEmailComposeRegistry?: {
      initAll?: (root?: ParentNode) => void;
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
  subject: "Subject",
  html_content: "HTML content",
  text_content: "Text content",
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

function fieldRows(field: EmailTemplateEditorField | null): number {
  return Number.parseInt(field?.attrs.rows || "12", 10);
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

function formatVariableToken(name: string): string {
  return `{{ ${name} }}`;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const htmlPreviewSrcdoc = computed(() => {
  const html = payload.value?.compose.preview.html || "";
  return html || '<span class="text-muted">No preview yet.</span>';
});

const textPreviewSrcdoc = computed(() => {
  const text = payload.value?.compose.preview.text || "";
  if (!text) {
    return '<span class="text-muted">No preview yet.</span>';
  }
  return `<pre style="margin:0;white-space:pre-wrap;font-family:inherit;">${escapeHtml(text)}</pre>`;
});

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

      <div class="row">
        <div class="col-lg-12">
          <div
            class="templated-email-compose"
            data-templated-email-compose
            data-compose-skip-initial-preview-refresh="1"
            :data-compose-preview-url="bootstrap.previewUrl"
          >
            <div class="card card-outline card-success">
              <div class="card-header">
                <div class="d-flex align-items-center justify-content-between flex-wrap" style="gap: .5rem;">
                  <h3 class="card-title mb-0">Compose</h3>
                  <span class="badge badge-warning d-none" data-compose-unsaved-badge>Unsaved changes</span>
                </div>
              </div>
              <div class="card-body">
                <div class="row">
                  <div class="col-md-6">
                    <div class="text-muted small mb-2">Edit the template subject and body.</div>

                    <div class="form-group mb-2">
                      <label>Email template</label>
                      <select class="form-control" name="email_template_id">
                        <option value="">(None)</option>
                        <option
                          v-for="option in payload.compose.templateOptions"
                          :key="option.id"
                          :value="option.id"
                          :selected="option.id === payload.compose.selectedTemplateId"
                        >{{ option.name }}</option>
                      </select>
                      <small class="form-text text-muted">Selecting a template will populate subject + bodies.</small>
                    </div>
                  </div>

                  <div class="col-md-6">
                    <div class="card card-outline card-info mb-0">
                      <div class="card-header py-2">
                        <h3 class="card-title">Available variables</h3>
                      </div>
                      <div class="card-body p-0" style="max-height: 240px; overflow: auto;">
                        <table v-if="payload.compose.availableVariables.length" class="table table-sm table-striped mb-0">
                          <thead>
                            <tr>
                              <th style="width: 45%">Variable</th>
                              <th>Example</th>
                            </tr>
                          </thead>
                          <tbody>
                            <tr v-for="variable in payload.compose.availableVariables" :key="variable.name">
                              <td><code>{{ formatVariableToken(variable.name) }}</code></td>
                              <td><span class="text-monospace">{{ variable.example }}</span></td>
                            </tr>
                          </tbody>
                        </table>
                        <div v-else class="p-3 text-muted small">No variables available yet.</div>
                      </div>
                    </div>
                  </div>
                </div>

                <div class="row">
                  <div class="col-md-12">
                    <div class="form-group">
                      <label :for="templateField('subject')?.id">Subject:</label>
                      <input
                        v-if="templateField('subject')"
                        :id="templateField('subject')?.id"
                        v-model="templateField('subject')!.value"
                        name="subject"
                        type="text"
                        :class="fieldClass(templateField('subject'))"
                        :disabled="templateField('subject')?.disabled"
                        :required="templateField('subject')?.required"
                        v-bind="fieldAttrs(templateField('subject'))"
                      >
                      <div v-for="fieldError in templateField('subject')?.errors || []" :key="fieldError" class="invalid-feedback d-block">{{ fieldError }}</div>
                    </div>
                  </div>
                </div>

                <div class="row">
                  <div class="col-md">
                    <label :for="templateField('html_content')?.id">HTML content</label>
                    <textarea
                      v-if="templateField('html_content')"
                      :id="templateField('html_content')?.id"
                      v-model="templateField('html_content')!.value"
                      name="html_content"
                      :rows="fieldRows(templateField('html_content'))"
                      :class="fieldClass(templateField('html_content'))"
                      :disabled="templateField('html_content')?.disabled"
                      v-bind="fieldAttrs(templateField('html_content'))"
                    />
                  </div>

                  <div class="col-md-auto d-flex align-items-center justify-content-center px-1">
                    <button
                      type="button"
                      class="btn btn-outline-secondary btn-sm"
                      data-compose-action="copy-html-to-text"
                      title="Copy HTML -> Text (strip formatting)"
                      aria-label="Copy HTML to Text"
                      style="min-width: 3rem;"
                    >
                      &gt;
                    </button>
                  </div>

                  <div class="col-md">
                    <label :for="templateField('text_content')?.id">Text content</label>
                    <textarea
                      v-if="templateField('text_content')"
                      :id="templateField('text_content')?.id"
                      v-model="templateField('text_content')!.value"
                      name="text_content"
                      :rows="fieldRows(templateField('text_content'))"
                      :class="fieldClass(templateField('text_content'))"
                      :disabled="templateField('text_content')?.disabled"
                      v-bind="fieldAttrs(templateField('text_content'))"
                    />
                  </div>
                </div>

                <hr>

                <div class="row">
                  <div class="col-md-6">
                    <h5>Rendered preview (HTML, first recipient)</h5>
                    <div class="border rounded p-2 bg-white" style="min-height: 140px;" data-compose-preview="html">
                      <iframe
                        data-compose-preview-iframe="1"
                        title="Rendered HTML preview"
                        sandbox="allow-popups"
                        referrerpolicy="no-referrer"
                        style="display:block;width:100%;height:400px;border:0;background:#fff;"
                        :srcdoc="htmlPreviewSrcdoc"
                      />
                    </div>
                  </div>
                  <div class="col-md-6">
                    <h5>Rendered preview (text, first recipient)</h5>
                    <div class="border rounded p-2 bg-white" style="min-height: 140px;" data-compose-preview="text">
                      <iframe
                        data-compose-preview-iframe="1"
                        title="Rendered text preview"
                        sandbox="allow-popups"
                        referrerpolicy="no-referrer"
                        style="display:block;width:100%;height:400px;border:0;background:#fff;"
                        :srcdoc="textPreviewSrcdoc"
                      />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

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
