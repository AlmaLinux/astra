<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";

import { fetchSendMailPayload, type SendMailBootstrap, type SendMailField, type SendMailFieldOption, type SendMailPayload } from "./types";

declare global {
  interface Window {
    TemplatedEmailComposeRegistry?: {
      initAll?: (root?: ParentNode) => void;
    };
    SendMailPage?: {
      init?: () => void;
    };
  }
}

const props = defineProps<{
  bootstrap: SendMailBootstrap;
}>();

const payload = ref<SendMailPayload | null>(props.bootstrap.initialPayload);
const loadError = ref("");

const fieldByName = computed<Record<string, SendMailField>>(() => {
  const fields = payload.value?.form.fields || [];
  return Object.fromEntries(fields.map((field) => [field.name, field]));
});

function field(name: string): SendMailField | null {
  return fieldByName.value[name] || null;
}

function stringFieldValue(name: string): string {
  const value = field(name)?.value;
  return Array.isArray(value) ? value.join(", ") : String(value || "");
}

function listFieldValue(name: string): string[] {
  const value = field(name)?.value;
  if (Array.isArray(value)) {
    return value.map((item) => String(item));
  }
  if (!value) {
    return [];
  }
  return [String(value)];
}

function fieldClass(name: string, fallback = "form-control"): string {
  return field(name)?.attrs.class || fallback;
}

function fieldRows(name: string, fallback = 12): number {
  return Number.parseInt(field(name)?.attrs.rows || String(fallback), 10);
}

function fieldAttrs(name: string): Record<string, string> {
  const attrs = { ...(field(name)?.attrs || {}) };
  delete attrs.class;
  delete attrs.rows;
  return attrs;
}

function fieldOptions(name: string): SendMailFieldOption[] {
  return field(name)?.options || [];
}

function fieldErrors(name: string): string[] {
  return field(name)?.errors || [];
}

function isMode(mode: string): boolean {
  const current = payload.value?.selectedRecipientMode || "";
  if (!current) {
    return mode === "group";
  }
  return current === mode;
}

const recipientCount = computed(() => payload.value?.recipientPreview.recipientCount || 0);
const availableVariables = computed(() => payload.value?.recipientPreview.variables || []);
const hasInitialPreview = computed(() => {
  const preview = payload.value?.compose.preview;
  if (!preview) {
    return false;
  }
  return [preview.subject, preview.html, preview.text].some((value) => String(value || "").trim().length > 0);
});

const actionNotice = computed(() => {
  const actionStatus = payload.value?.actionStatus || "";
  const actionLabel = {
    approved: "approved",
    accepted: "approved",
    rejected: "rejected",
    rfi: "placed on hold",
    on_hold: "placed on hold",
  }[actionStatus];

  if (!actionLabel) {
    return "";
  }

  return `This request has already been ${actionLabel}. No email has been sent yet. It is important to notify the requester, so please send the custom email now.`;
});

const showExtraOptions = computed(() => {
  return ["cc", "bcc", "reply_to"].some((name) => stringFieldValue(name).trim().length > 0);
});

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
    payload.value = await fetchSendMailPayload(props.bootstrap.apiUrl);
  } catch {
    loadError.value = "Unable to load send mail right now.";
  }
}

function initEnhancements(): void {
  window.TemplatedEmailComposeRegistry?.initAll?.(document);
  window.SendMailPage?.init?.();
}

watch(
  () => payload.value,
  async (value) => {
    if (value === null) {
      return;
    }

    await nextTick();
    initEnhancements();
  },
  { immediate: true },
);

onMounted(async () => {
  await loadPayload();
});
</script>

<template>
  <div data-send-mail-page-vue-root>
    <div v-if="loadError" class="alert alert-danger" role="alert">{{ loadError }}</div>
    <div v-else-if="!payload" class="text-muted">Loading send mail...</div>
    <form v-else id="send-mail-form" :action="bootstrap.submitUrl" method="post" enctype="multipart/form-data">
      <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
      <input type="hidden" name="action" id="send-mail-action" value="">
      <input type="hidden" name="save_as_name" id="send-mail-save-as-name" value="">
      <input type="hidden" name="recipient_mode" id="send-mail-recipient-mode" :value="payload.selectedRecipientMode">
      <input type="hidden" name="action_status" :value="payload.actionStatus">
      <input type="hidden" name="invitation_action" :id="field('invitation_action')?.id || 'id_invitation_action'" value="" :value="stringFieldValue('invitation_action')">
      <input type="hidden" name="invitation_org_id" :id="field('invitation_org_id')?.id || 'id_invitation_org_id'" value="" :value="stringFieldValue('invitation_org_id')">
      <input type="hidden" name="extra_context_json" :id="field('extra_context_json')?.id || 'id_extra_context_json'" :value="stringFieldValue('extra_context_json')">
      <input type="hidden" id="send-mail-has-saved-csv" :value="payload.hasSavedCsvRecipients ? '1' : '0'">
      <input type="hidden" id="send-mail-autoload-template-id" :value="payload.createdTemplateId === null ? '' : String(payload.createdTemplateId)">

      <div v-if="actionNotice" class="alert alert-success" role="alert">{{ actionNotice }}</div>
      <div v-if="payload.form.nonFieldErrors.length" class="alert alert-danger" role="alert">
        <div v-for="errorItem in payload.form.nonFieldErrors" :key="errorItem">{{ errorItem }}</div>
      </div>

      <div class="row">
        <div class="col-lg-12">
          <div class="card card-outline card-primary">
            <div class="card-header">
              <h3 class="card-title">Recipients</h3>
            </div>
            <div class="card-body">
              <div class="row">
                <div class="col-md-6">
                  <ul class="nav nav-tabs" id="send-mail-recipient-tabs" role="tablist">
                    <li class="nav-item">
                      <a class="nav-link" :class="{ active: isMode('group') }" id="send-mail-recipients-group-tab" data-toggle="pill" href="#send-mail-recipients-group" role="tab" aria-controls="send-mail-recipients-group" aria-selected="true" data-recipient-mode="group">Group</a>
                    </li>
                    <li class="nav-item">
                      <a class="nav-link" :class="{ active: isMode('users') }" id="send-mail-recipients-users-tab" data-toggle="pill" href="#send-mail-recipients-users" role="tab" aria-controls="send-mail-recipients-users" aria-selected="false" data-recipient-mode="users">Users</a>
                    </li>
                    <li class="nav-item">
                      <a class="nav-link" :class="{ active: isMode('csv') }" id="send-mail-recipients-csv-tab" data-toggle="pill" href="#send-mail-recipients-csv" role="tab" aria-controls="send-mail-recipients-csv" aria-selected="false" data-recipient-mode="csv">CSV file</a>
                    </li>
                    <li class="nav-item">
                      <a class="nav-link" :class="{ active: isMode('manual') }" id="send-mail-recipients-manual-tab" data-toggle="pill" href="#send-mail-recipients-manual" role="tab" aria-controls="send-mail-recipients-manual" aria-selected="false" data-recipient-mode="manual">Manual</a>
                    </li>
                  </ul>
                  <div class="tab-content mb-3" id="send-mail-recipient-tab-content">
                    <div class="tab-pane fade" :class="{ 'active show': isMode('group') }" id="send-mail-recipients-group" role="tabpanel" aria-labelledby="send-mail-recipients-group-tab">
                      <div class="mt-3">
                        <div class="form-group mb-0">
                          <label :for="field('group_cn')?.id || 'id_group_cn'">Group cn:</label>
                          <select :id="field('group_cn')?.id || 'id_group_cn'" name="group_cn" :class="fieldClass('group_cn')" v-bind="fieldAttrs('group_cn')">
                            <option value="">(Select a group)</option>
                            <option v-for="option in fieldOptions('group_cn')" :key="option.value" :value="option.value" :selected="stringFieldValue('group_cn') === option.value">{{ option.label }}</option>
                          </select>
                          <small class="form-text text-muted">Includes nested group members.</small>
                          <div v-for="errorItem in fieldErrors('group_cn')" :key="errorItem" class="invalid-feedback d-block">{{ errorItem }}</div>
                        </div>
                      </div>
                    </div>
                    <div class="tab-pane fade" :class="{ 'active show': isMode('users') }" id="send-mail-recipients-users" role="tabpanel" aria-labelledby="send-mail-recipients-users-tab">
                      <div class="mt-3">
                        <div class="form-group mb-0">
                          <label :for="field('user_usernames')?.id || 'id_user_usernames'">User usernames:</label>
                          <select :id="field('user_usernames')?.id || 'id_user_usernames'" name="user_usernames" :class="fieldClass('user_usernames')" multiple v-bind="fieldAttrs('user_usernames')">
                            <option v-for="option in fieldOptions('user_usernames')" :key="option.value" :value="option.value" :selected="listFieldValue('user_usernames').includes(option.value)">{{ option.label }}</option>
                          </select>
                          <small class="form-text text-muted">Select one or more users.</small>
                          <div v-for="errorItem in fieldErrors('user_usernames')" :key="errorItem" class="invalid-feedback d-block">{{ errorItem }}</div>
                        </div>
                      </div>
                    </div>
                    <div class="tab-pane fade" :class="{ 'active show': isMode('csv') }" id="send-mail-recipients-csv" role="tabpanel" aria-labelledby="send-mail-recipients-csv-tab">
                      <div class="mt-3">
                        <div class="form-group mb-0">
                          <label :for="field('csv_file')?.id || 'id_csv_file'">Csv file:</label>
                          <input :id="field('csv_file')?.id || 'id_csv_file'" type="file" name="csv_file" :class="fieldClass('csv_file')" v-bind="fieldAttrs('csv_file')">
                          <small class="form-text text-muted">CSV should include an Email column. If you previously uploaded a CSV, you can leave this blank to reuse it.</small>
                          <div v-for="errorItem in fieldErrors('csv_file')" :key="errorItem" class="invalid-feedback d-block">{{ errorItem }}</div>
                        </div>
                        <button type="submit" class="mt-3 btn btn-primary" id="send-mail-load-file-btn" onclick="document.getElementById('send-mail-action').value='preview'" title="Load recipients from CSV">Load file</button>
                      </div>
                    </div>
                    <div class="tab-pane fade" :class="{ 'active show': isMode('manual') }" id="send-mail-recipients-manual" role="tabpanel" aria-labelledby="send-mail-recipients-manual-tab">
                      <div class="mt-3">
                        <div class="form-group mb-0">
                          <label :for="field('manual_to')?.id || 'id_manual_to'">Manual to:</label>
                          <input :id="field('manual_to')?.id || 'id_manual_to'" type="text" name="manual_to" :class="fieldClass('manual_to')" :value="stringFieldValue('manual_to')" v-bind="fieldAttrs('manual_to')">
                          <small class="form-text text-muted">Comma-separated email addresses.</small>
                          <div v-for="errorItem in fieldErrors('manual_to')" :key="errorItem" class="invalid-feedback d-block">{{ errorItem }}</div>
                        </div>
                      </div>
                    </div>
                  </div>

                  <div class="alert alert-warning mt-2 d-none" id="send-mail-recipients-inline-warning" role="alert"></div>

                  <div class="mb-2">
                    <a class="text-muted" id="send-mail-extra-options-toggle" data-toggle="collapse" href="#send-mail-extra-options" role="button" :aria-expanded="showExtraOptions ? 'true' : 'false'" aria-controls="send-mail-extra-options">
                      <span aria-hidden="true" class="mr-1 send-mail-collapse-chevron">▸</span>
                      Additional options
                    </a>
                    <div class="collapse mt-2" :class="{ show: showExtraOptions }" id="send-mail-extra-options">
                      <div class="card card-body">
                        <div class="form-group">
                          <label :for="field('cc')?.id || 'id_cc'">Cc:</label>
                          <input :id="field('cc')?.id || 'id_cc'" type="text" name="cc" :class="fieldClass('cc')" :value="stringFieldValue('cc')" v-bind="fieldAttrs('cc')">
                          <small class="form-text text-muted">Comma-separated email addresses.</small>
                          <div v-for="errorItem in fieldErrors('cc')" :key="errorItem" class="invalid-feedback d-block">{{ errorItem }}</div>
                        </div>
                        <div class="form-group">
                          <label :for="field('bcc')?.id || 'id_bcc'">Bcc:</label>
                          <input :id="field('bcc')?.id || 'id_bcc'" type="text" name="bcc" :class="fieldClass('bcc')" :value="stringFieldValue('bcc')" v-bind="fieldAttrs('bcc')">
                          <small class="form-text text-muted">Comma-separated email addresses.</small>
                          <div v-for="errorItem in fieldErrors('bcc')" :key="errorItem" class="invalid-feedback d-block">{{ errorItem }}</div>
                        </div>
                        <div class="form-group mb-0">
                          <label :for="field('reply_to')?.id || 'id_reply_to'">Reply to:</label>
                          <input :id="field('reply_to')?.id || 'id_reply_to'" type="text" name="reply_to" :class="fieldClass('reply_to')" :value="stringFieldValue('reply_to')" v-bind="fieldAttrs('reply_to')">
                          <small class="form-text text-muted">Comma-separated email addresses.</small>
                          <div v-for="errorItem in fieldErrors('reply_to')" :key="errorItem" class="invalid-feedback d-block">{{ errorItem }}</div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                <div class="col-md-6">
                  <div class="alert alert-info mb-3">
                    Recipient count:
                    <strong>
                      <span id="send-mail-recipient-count" :data-server-count="recipientCount">{{ recipientCount }}</span>
                    </strong>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="row">
        <div class="col-lg-12">
          <div class="templated-email-compose" data-templated-email-compose :data-compose-preview-url="bootstrap.previewUrl" :data-compose-skip-initial-preview-refresh="hasInitialPreview ? '1' : null">
            <div class="card card-outline card-success">
              <div class="card-header">
                <div class="d-flex align-items-center justify-content-between flex-wrap" style="gap: .5rem;">
                  <h3 class="card-title mb-0">Compose email</h3>
                  <span class="badge badge-warning d-none" data-compose-unsaved-badge>Unsaved changes</span>
                </div>
              </div>
              <div class="card-body">
                <div class="row">
                  <div class="col-md-6">
                    <div class="text-muted small mb-2">Use recipient variables to personalize the subject and content.</div>
                    <div class="form-group mb-2">
                      <label>Email template</label>
                      <select class="form-control" name="email_template_id">
                        <option value="">(None)</option>
                        <option v-for="template in payload.templates" :key="template.id" :value="template.id" :selected="payload.compose.selectedTemplateId === template.id">{{ template.name }}</option>
                      </select>
                      <small class="form-text text-muted">Selecting a template will populate subject + bodies.</small>
                      <div class="mt-2">
                        <button type="button" class="btn btn-outline-secondary btn-sm" data-compose-action="restore" title="Restore last saved content" disabled>Restore</button>
                        <button type="button" class="btn btn-outline-primary btn-sm" data-compose-action="save" title="Save to selected template">Save</button>
                        <button type="button" class="btn btn-outline-primary btn-sm" data-compose-action="save-as" title="Save as a new template">Save as…</button>
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
                      <label :for="field('subject')?.id || 'id_subject'">Subject:</label>
                      <input :id="field('subject')?.id || 'id_subject'" type="text" name="subject" :class="fieldClass('subject')" :value="stringFieldValue('subject')" v-bind="fieldAttrs('subject')">
                      <div v-for="errorItem in fieldErrors('subject')" :key="errorItem" class="invalid-feedback d-block">{{ errorItem }}</div>
                    </div>
                  </div>
                </div>

                <div class="row">
                  <div class="col-md">
                    <label :for="field('html_content')?.id || 'id_html_content'">HTML content</label>
                    <textarea :id="field('html_content')?.id || 'id_html_content'" name="html_content" :rows="fieldRows('html_content')" :class="fieldClass('html_content')" v-bind="fieldAttrs('html_content')">{{ stringFieldValue('html_content') }}</textarea>
                  </div>
                  <div class="col-md-auto d-flex align-items-center justify-content-center px-1">
                    <button type="button" class="btn btn-outline-secondary btn-sm" data-compose-action="copy-html-to-text" title="Copy HTML -> Text (strip formatting)" aria-label="Copy HTML to Text" style="min-width: 3rem;">&gt;</button>
                  </div>
                  <div class="col-md">
                    <label :for="field('text_content')?.id || 'id_text_content'">Text content</label>
                    <textarea :id="field('text_content')?.id || 'id_text_content'" name="text_content" :rows="fieldRows('text_content')" :class="fieldClass('text_content')" v-bind="fieldAttrs('text_content')">{{ stringFieldValue('text_content') }}</textarea>
                  </div>
                </div>

                <hr>

                <div class="row">
                  <div class="col-md-6">
                    <h5>Rendered preview (HTML, first recipient)</h5>
                    <div class="border rounded p-2 bg-white" style="min-height: 140px;" data-compose-preview="html">
                      <iframe data-compose-preview-iframe="1" title="Rendered HTML preview" sandbox="allow-popups" referrerpolicy="no-referrer" style="display:block;width:100%;height:400px;border:0;background:#fff;" :srcdoc="htmlPreviewSrcdoc" />
                    </div>
                  </div>
                  <div class="col-md-6">
                    <h5>Rendered preview (text, first recipient)</h5>
                    <div class="border rounded p-2 bg-white" style="min-height: 140px;" data-compose-preview="text">
                      <iframe data-compose-preview-iframe="1" title="Rendered text preview" sandbox="allow-popups" referrerpolicy="no-referrer" style="display:block;width:100%;height:400px;border:0;background:#fff;" :srcdoc="textPreviewSrcdoc" />
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div data-compose-modal="save">
              <div class="modal fade" tabindex="-1" role="dialog" aria-hidden="true">
                <div class="modal-dialog" role="document">
                  <div class="modal-content">
                    <div class="modal-header">
                      <h5 class="modal-title">Save email template</h5>
                      <button type="button" class="close" data-dismiss="modal" aria-label="Close" title="Close dialog"><span aria-hidden="true">&times;</span></button>
                    </div>
                    <div class="modal-body">
                      <div class="mb-3">Overwrite the selected email template with the current subject and contents?</div>
                      <form method="post" action="#" class="m-0">
                        <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
                        <div class="d-flex justify-content-between">
                          <button type="button" class="btn btn-secondary" data-dismiss="modal" aria-label="Cancel" title="Close dialog without applying changes">Cancel</button>
                          <button type="submit" class="btn btn-primary" title="Confirm and apply this action">Save</button>
                        </div>
                      </form>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div data-compose-modal="save-as">
              <div class="modal fade" tabindex="-1" role="dialog" aria-hidden="true">
                <div class="modal-dialog" role="document" style="text-align: left;">
                  <div class="modal-content">
                    <div class="modal-header">
                      <h5 class="modal-title">Save email template as…</h5>
                      <button type="button" class="close" data-dismiss="modal" aria-label="Close" title="Close dialog"><span aria-hidden="true">&times;</span></button>
                    </div>
                    <div class="modal-body">
                      <div class="mb-3">Create a new template from the current subject and contents?</div>
                      <form method="post" action="#" class="m-0">
                        <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
                        <div class="form-group">
                          <label>New template name</label>
                          <input type="text" class="form-control" name="name" required autocomplete="off">
                        </div>
                        <div class="d-flex justify-content-between">
                          <button type="button" class="btn btn-secondary" data-dismiss="modal" aria-label="Cancel" title="Close dialog without applying changes">Cancel</button>
                          <button type="submit" class="btn btn-primary" title="Confirm and apply this action">Create</button>
                        </div>
                      </form>
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
          <div class="card card-outline card-danger">
            <div class="card-header">
              <h3 class="card-title">Send emails</h3>
            </div>
            <div class="card-body d-flex align-items-center justify-content-center flex-wrap">
              <div>
                <button type="button" class="btn btn-danger" id="send-mail-send-btn" title="Send emails now" :disabled="recipientCount === 0">
                  Send <b><span id="send-mail-send-count" :data-server-count="recipientCount">{{ recipientCount }}</span></b> Email{{ recipientCount === 1 ? '' : 's' }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </form>

    <div v-if="payload" class="modal fade" id="send-mail-send-confirm-modal" tabindex="-1" role="dialog" aria-hidden="true">
      <div class="modal-dialog" role="document">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title">Send emails?</h5>
            <button type="button" class="close" data-dismiss="modal" aria-label="Close" title="Close dialog"><span aria-hidden="true">&times;</span></button>
          </div>
          <div class="modal-body">
            <div class="mb-3">Queue {{ recipientCount }} email{{ recipientCount === 1 ? '' : 's' }} for delivery using the current recipients and message contents?</div>
            <div class="d-flex justify-content-between">
              <button type="button" class="btn btn-secondary" data-dismiss="modal" aria-label="Cancel" title="Cancel sending">Cancel</button>
              <button type="button" class="btn btn-danger" id="send-mail-send-confirm-btn" title="Send emails now">Send</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style>
#send-mail-extra-options-toggle .send-mail-collapse-chevron {
  display: inline-block;
  transition: transform 150ms ease-in-out;
}

#send-mail-extra-options-toggle[aria-expanded="true"] .send-mail-collapse-chevron {
  transform: rotate(90deg);
}
</style>
