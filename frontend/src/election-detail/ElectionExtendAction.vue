<script setup lang="ts">
import { computed, ref } from "vue";

import type { ElectionExtendActionBootstrap } from "./types";

const props = defineProps<{
  bootstrap: ElectionExtendActionBootstrap;
}>();

const isOpen = ref(false);
const confirmValue = ref("");
const endDatetime = ref(props.bootstrap.currentEndDateTimeValue);
const isSubmitting = ref(false);
const errors = ref<string[]>([]);

const confirmMatches = computed(() => {
  return confirmValue.value.trim().localeCompare(props.bootstrap.electionName, undefined, { sensitivity: "accent" }) === 0
    || confirmValue.value.trim().toLowerCase() === props.bootstrap.electionName.trim().toLowerCase();
});

const canSubmit = computed(() => {
  return confirmMatches.value && endDatetime.value.trim().length > 0 && !isSubmitting.value;
});

function readCsrfToken(): string {
  for (const cookie of document.cookie.split(";")) {
    const trimmed = cookie.trim();
    if (trimmed.startsWith("csrftoken=")) {
      return decodeURIComponent(trimmed.slice("csrftoken=".length));
    }
  }
  return "";
}

function closeModal(): void {
  if (isSubmitting.value) {
    return;
  }
  isOpen.value = false;
  errors.value = [];
}

async function submit(): Promise<void> {
  if (!canSubmit.value) {
    return;
  }

  isSubmitting.value = true;
  errors.value = [];

  try {
    const response = await fetch(props.bootstrap.extendApiUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": readCsrfToken(),
      },
      body: JSON.stringify({
        confirm: confirmValue.value,
        end_datetime: endDatetime.value,
      }),
    });

    if (!response.ok) {
      const payload = (await response.json().catch(() => ({ errors: ["Unable to extend the election right now."] }))) as {
        errors?: string[];
      };
      errors.value = payload.errors && payload.errors.length > 0 ? payload.errors : ["Unable to extend the election right now."];
      return;
    }

    window.location.reload();
  } catch {
    errors.value = ["Unable to extend the election right now."];
  } finally {
    isSubmitting.value = false;
  }
}
</script>

<template>
  <div data-election-extend-action-vue-root>
    <button
      type="button"
      class="btn btn-warning btn-block mb-2"
      title="Extend the election end date if needed"
      @click="isOpen = true"
    >
      Extend Election
    </button>

    <div v-if="isOpen" class="modal fade show d-block" tabindex="-1" role="dialog" aria-modal="true" aria-labelledby="extend-election-modal-label">
      <div class="modal-dialog" role="document">
        <div class="modal-content">
          <form @submit.prevent="submit">
            <div class="modal-header">
              <h5 id="extend-election-modal-label" class="modal-title">Extend election?</h5>
              <button type="button" class="close" aria-label="Close" title="Close dialog" @click="closeModal">
                <span aria-hidden="true">&times;</span>
              </button>
            </div>
            <div class="modal-body">
              <div v-if="errors.length > 0" class="alert alert-danger" role="alert">
                <p v-for="error in errors" :key="error" class="mb-0">{{ error }}</p>
              </div>

              <p class="mb-3">Set a new end datetime. It must be later than the current end.</p>

              <div class="form-group mb-0">
                <label for="extend-end-datetime">New end datetime</label>
                <input
                  id="extend-end-datetime"
                  v-model="endDatetime"
                  name="end_datetime"
                  type="datetime-local"
                  class="form-control js-datetime-picker"
                  :min="bootstrap.currentEndDateTimeValue"
                  required
                >
                <div class="text-muted small mt-1">Current end: {{ bootstrap.currentEndDateTimeDisplay }}</div>
              </div>

              <hr>

              <div class="form-group mb-0">
                <label for="extend-confirm">Type the election name to confirm</label>
                <div class="text-muted small mb-2">
                  <strong>Election name:</strong> {{ bootstrap.electionName }}
                </div>
                <input
                  id="extend-confirm"
                  v-model="confirmValue"
                  name="confirm"
                  type="text"
                  class="form-control"
                  :placeholder="bootstrap.electionName"
                  required
                >
              </div>
            </div>
            <div class="modal-footer">
              <button type="button" class="btn btn-outline-secondary" title="Close dialog without extending" @click="closeModal">Cancel</button>
              <button id="extend-submit" type="submit" class="btn btn-warning" :disabled="!canSubmit" :aria-disabled="!canSubmit" title="Extend the election end date">
                {{ isSubmitting ? "Extending..." : "Extend Election" }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <div v-if="isOpen" class="modal-backdrop fade show"></div>
  </div>
</template>