<script setup lang="ts">
import { onMounted, ref } from "vue";

import { fetchEmailTemplatesPayload, replaceTemplateToken, type EmailTemplateListItem, type EmailTemplatesBootstrap, type EmailTemplatesPayload } from "./types";

const props = defineProps<{
  bootstrap: EmailTemplatesBootstrap;
}>();

const payload = ref<EmailTemplatesPayload | null>(props.bootstrap.initialPayload);
const loadError = ref("");
const deleteTarget = ref<EmailTemplateListItem | null>(null);

async function loadPayload(): Promise<void> {
  if (payload.value !== null || !props.bootstrap.apiUrl) {
    return;
  }
  try {
    payload.value = await fetchEmailTemplatesPayload(props.bootstrap.apiUrl);
  } catch {
    loadError.value = "Unable to load email templates right now.";
  }
}

function editUrl(template: EmailTemplateListItem): string {
  return replaceTemplateToken(props.bootstrap.editUrlTemplate, template.id);
}

function deleteUrl(template: EmailTemplateListItem): string {
  return replaceTemplateToken(props.bootstrap.deleteUrlTemplate, template.id);
}

function openDeleteModal(template: EmailTemplateListItem): void {
  deleteTarget.value = template;
}

onMounted(async () => {
  await loadPayload();
});
</script>

<template>
  <div data-email-templates-vue-root>
    <div v-if="loadError" class="alert alert-danger" role="alert">{{ loadError }}</div>
    <div v-else-if="!payload" class="text-muted">Loading templates...</div>
    <div v-else class="row">
      <div class="col-lg-12">
        <div class="card card-outline card-primary">
          <div class="card-header d-flex align-items-center">
            <h3 class="card-title mb-0">Email Templates</h3>
            <a class="btn btn-primary btn-sm ml-auto" :href="bootstrap.createUrl" title="Create a new email template">New template</a>
          </div>
          <div class="card-body p-0">
            <div class="table-responsive">
              <table class="table table-striped mb-0">
                <thead>
                  <tr>
                    <th style="width: 30%">Name</th>
                    <th>Description</th>
                    <th style="width: 18%">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="template in payload.templates" :key="template.id">
                    <td>
                      <span class="text-monospace">{{ template.name }}</span>
                      <span v-if="template.isLocked" class="badge badge-secondary ml-2" title="Referenced by app configuration">Locked</span>
                    </td>
                    <td class="text-muted">{{ template.description }}</td>
                    <td>
                      <a class="btn btn-outline-primary btn-sm" :href="editUrl(template)" title="Edit this template">Edit</a>
                      <button
                        v-if="template.isLocked"
                        type="button"
                        class="btn btn-outline-danger btn-sm"
                        disabled
                        aria-disabled="true"
                        title="This template is referenced by the app configuration and cannot be deleted."
                      >
                        Delete
                      </button>
                      <button
                        v-else
                        type="button"
                        class="btn btn-outline-danger btn-sm"
                        data-toggle="modal"
                        data-target="#email-template-delete-modal"
                        title="Delete this template"
                        @click="openDeleteModal(template)"
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                  <tr v-if="payload.templates.length === 0">
                    <td colspan="3" class="p-3 text-muted">No templates yet.</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="modal fade" id="email-template-delete-modal" tabindex="-1" role="dialog" aria-hidden="true" aria-labelledby="email-template-delete-modal-title">
      <div class="modal-dialog" role="document">
        <div class="modal-content">
          <div class="modal-header">
            <h5 id="email-template-delete-modal-title" class="modal-title">Delete email template?</h5>
            <button type="button" class="close" data-dismiss="modal" aria-label="Close" title="Close dialog">
              <span aria-hidden="true">&times;</span>
            </button>
          </div>
          <div class="modal-body">
            <div class="mb-3">
              Delete template: <strong>{{ deleteTarget?.name || "" }}</strong>
              <p>This cannot be undone.</p>
            </div>
            <form method="post" :action="deleteTarget ? deleteUrl(deleteTarget) : '#'" class="m-0">
              <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
              <div class="d-flex justify-content-between">
                <button type="button" class="btn btn-secondary" data-dismiss="modal" aria-label="Cancel" title="Close dialog without applying changes">Cancel</button>
                <button type="submit" class="btn btn-danger" title="Confirm and apply this action" :disabled="deleteTarget === null">Delete</button>
              </div>
            </form>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>