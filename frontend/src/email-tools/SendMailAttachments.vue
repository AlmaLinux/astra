<script setup lang="ts">
import { ref } from "vue";

import type { SendMailAttachment } from "./types";

// Kept in its own component: the Send Mail form's inputs are uncontrolled, and any
// re-render of the page would reset what the operator typed into them.
const props = defineProps<{
  initialAttachments: SendMailAttachment[];
  uploadUrl: string;
  csrfToken: string;
}>();

const attachments = ref<SendMailAttachment[]>([...props.initialAttachments]);
const isUploading = ref(false);
const error = ref("");

// Files upload as soon as they are picked, so the picker is free for more and the
// files survive later re-renders as plain references.
async function upload(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files ?? []);
  if (files.length === 0) {
    return;
  }

  const body = new FormData();
  for (const file of files) {
    body.append("files", file);
  }
  // The server validates size and duplicate names against what is already attached.
  for (const attachment of attachments.value) {
    body.append("attachment", attachment.token);
  }

  error.value = "";
  isUploading.value = true;
  try {
    const response = await fetch(props.uploadUrl, {
      method: "POST",
      headers: { Accept: "application/json", "X-CSRFToken": props.csrfToken },
      body,
      credentials: "same-origin",
    });
    const result = (await response.json().catch(() => ({}))) as { attachments?: SendMailAttachment[]; error?: string };
    if (!response.ok || !result.attachments) {
      error.value = result.error || "Unable to attach the file.";
      return;
    }
    attachments.value = [...attachments.value, ...result.attachments];
  } catch {
    error.value = "Unable to attach the file.";
  } finally {
    isUploading.value = false;
    input.value = "";
  }
}

function remove(token: string): void {
  attachments.value = attachments.value.filter((attachment) => attachment.token !== token);
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
</script>

<template>
  <div class="form-group mb-0">
    <label for="send-mail-attachments">Attachments:</label>
    <ul v-if="attachments.length" class="list-unstyled mb-2">
      <li v-for="attachment in attachments" :key="attachment.token" class="d-flex align-items-center mb-1">
        <input type="hidden" name="attachment" :value="attachment.token">
        <i class="fas fa-paperclip text-muted mr-2" aria-hidden="true" />
        <span class="text-break">{{ attachment.name }}</span>
        <span class="text-muted small ml-2">{{ formatFileSize(attachment.size) }}</span>
        <button
          type="button"
          class="btn btn-link btn-sm text-danger ml-auto p-0"
          :aria-label="`Remove ${attachment.name}`"
          :title="`Remove ${attachment.name}`"
          @click="remove(attachment.token)"
        >&times;</button>
      </li>
    </ul>
    <input
      id="send-mail-attachments"
      type="file"
      class="form-control-file"
      multiple
      :disabled="isUploading"
      @change="upload"
    >
    <div v-if="isUploading" class="small text-muted mt-1">Uploading...</div>
    <div v-if="error" class="invalid-feedback d-block">{{ error }}</div>
    <small class="form-text text-muted">Sent to every recipient.</small>
  </div>
</template>
