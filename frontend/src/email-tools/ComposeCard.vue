<script setup lang="ts">
import { computed } from "vue";

import type { ComposeFieldSpec, ComposePreview, ComposeTemplateOption, ComposeVariable } from "./types";

const props = withDefaults(
  defineProps<{
    title?: string;
    helpText?: string;
    templateOptions: ComposeTemplateOption[];
    selectedTemplateId: number | null;
    availableVariables: ComposeVariable[];
    previewUrl: string;
    skipInitialPreviewRefresh?: boolean;
    preview: ComposePreview;
    templateSelectorDisabled?: boolean;
    showCard?: boolean;
    showSaveButtons?: boolean;
    subjectField: ComposeFieldSpec;
    htmlContentField: ComposeFieldSpec;
    textContentField: ComposeFieldSpec;
  }>(),
  {
    title: "Compose",
    helpText: "Edit the template subject and body.",
    skipInitialPreviewRefresh: false,
    templateSelectorDisabled: false,
    showCard: true,
    showSaveButtons: false,
  },
);

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
  return props.preview.html || '<span class="text-muted">No preview yet.</span>';
});

const textPreviewSrcdoc = computed(() => {
  if (!props.preview.text) {
    return '<span class="text-muted">No preview yet.</span>';
  }
  return `<pre style="margin:0;white-space:pre-wrap;font-family:inherit;">${escapeHtml(props.preview.text)}</pre>`;
});
</script>

<template>
  <div class="row">
    <div class="col-lg-12">
      <div
        class="templated-email-compose"
        data-templated-email-compose
        :data-compose-preview-url="previewUrl"
        :data-compose-skip-initial-preview-refresh="skipInitialPreviewRefresh ? '1' : null"
      >
        <slot name="hidden-fields" />
        <div :class="showCard ? 'card card-outline card-success' : undefined">
          <div v-if="showCard" class="card-header">
            <div class="d-flex align-items-center justify-content-between flex-wrap" style="gap: .5rem;">
              <h3 class="card-title mb-0">{{ title }}</h3>
              <span class="badge badge-warning d-none" data-compose-unsaved-badge>Unsaved changes</span>
            </div>
          </div>
          <div :class="showCard ? 'card-body' : undefined">
            <div class="row">
              <div class="col-md-6">
                <div class="text-muted small mb-2">{{ helpText }}</div>

                <div class="form-group mb-2">
                  <label>Email template</label>
                  <select class="form-control" name="email_template_id" :disabled="templateSelectorDisabled || undefined">
                    <option value="">(None)</option>
                    <option
                      v-for="option in templateOptions"
                      :key="option.id"
                      :value="option.id"
                      :selected="option.id === selectedTemplateId"
                    >{{ option.name }}</option>
                  </select>
                  <small class="form-text text-muted">Selecting a template will populate subject + bodies.</small>
                  <slot name="template-selector-extra" />
                  <div v-if="showSaveButtons" class="mt-2">
                    <button type="button" class="btn btn-outline-secondary btn-sm" data-compose-action="restore" title="Restore last saved content" disabled>Restore</button>
                    <button type="button" class="btn btn-outline-primary btn-sm" data-compose-action="save" title="Save to selected template">Save</button>
                    <button type="button" class="btn btn-outline-primary btn-sm" data-compose-action="save-as" title="Save as a new template">Save as&hellip;</button>
                  </div>
                </div>
              </div>

              <div class="col-md-6">
                <div class="card card-outline card-info mb-0">
                  <div class="card-header py-2">
                    <h3 class="card-title">Available variables</h3>
                  </div>
                  <div class="card-body p-0" style="max-height: 240px; overflow: auto;">
                    <table v-if="availableVariables.length" class="table table-sm table-striped mb-0">
                      <thead>
                        <tr>
                          <th style="width: 45%">Variable</th>
                          <th>Example</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="variable in availableVariables" :key="variable.name">
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
                  <label :for="subjectField.id">Subject:</label>
                  <input
                    :id="subjectField.id"
                    type="text"
                    name="subject"
                    :class="subjectField.cssClass"
                    :value="subjectField.value"
                    :disabled="subjectField.disabled || undefined"
                    :required="subjectField.required || undefined"
                    v-bind="subjectField.attrs"
                  >
                  <div v-for="errorItem in subjectField.errors" :key="errorItem" class="invalid-feedback d-block">{{ errorItem }}</div>
                </div>
              </div>
            </div>

            <div class="row">
              <div class="col-md">
                <label :for="htmlContentField.id">HTML content</label>
                <textarea
                  :id="htmlContentField.id"
                  name="html_content"
                  :rows="htmlContentField.rows"
                  :class="htmlContentField.cssClass"
                  :disabled="htmlContentField.disabled || undefined"
                  v-bind="htmlContentField.attrs"
                >{{ htmlContentField.value }}</textarea>
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
                <label :for="textContentField.id">Text content</label>
                <textarea
                  :id="textContentField.id"
                  name="text_content"
                  :rows="textContentField.rows"
                  :class="textContentField.cssClass"
                  :disabled="textContentField.disabled || undefined"
                  v-bind="textContentField.attrs"
                >{{ textContentField.value }}</textarea>
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

        <slot name="after-card" />

        <!-- Save-as modal (used by templated_email.js when save-as button is clicked) -->
        <div v-if="showSaveButtons" data-compose-modal="save-as">
          <div class="modal fade" tabindex="-1" role="dialog">
            <div class="modal-dialog modal-sm" role="document">
              <div class="modal-content">
                <form>
                  <div class="modal-header">
                    <h5 class="modal-title">Save as new template</h5>
                    <button type="button" class="close" data-dismiss="modal" aria-label="Close"><span aria-hidden="true">&times;</span></button>
                  </div>
                  <div class="modal-body">
                    <div class="form-group mb-0">
                      <label for="compose-save-as-name">Template name</label>
                      <input type="text" class="form-control" id="compose-save-as-name" name="name" required>
                    </div>
                  </div>
                  <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-dismiss="modal">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create</button>
                  </div>
                </form>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
