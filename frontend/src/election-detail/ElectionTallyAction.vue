<script setup lang="ts">
import { computed, ref } from "vue";

import type { ElectionTallyActionBootstrap } from "./types";

const props = defineProps<{
  bootstrap: ElectionTallyActionBootstrap;
}>();

const isOpen = ref(false);
const confirmValue = ref("");
const isSubmitting = ref(false);
const errors = ref<string[]>([]);

const confirmMatches = computed(() => {
  return confirmValue.value.trim().toLowerCase() === props.bootstrap.electionName.trim().toLowerCase();
});

const canSubmit = computed(() => {
  return confirmMatches.value && !isSubmitting.value;
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
    const response = await fetch(props.bootstrap.tallyApiUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": readCsrfToken(),
      },
      body: JSON.stringify({
        confirm: confirmValue.value,
      }),
    });

    if (!response.ok) {
      const payload = (await response.json().catch(() => ({ errors: ["Unable to tally the election right now."] }))) as {
        errors?: string[];
      };
      errors.value = payload.errors && payload.errors.length > 0 ? payload.errors : ["Unable to tally the election right now."];
      return;
    }

    window.location.reload();
  } catch {
    errors.value = ["Unable to tally the election right now."];
  } finally {
    isSubmitting.value = false;
  }
}
</script>

<template>
  <div data-election-tally-action-vue-root>
    <button
      type="button"
      class="btn btn-primary btn-block"
      title="Tally the closed election"
      @click="isOpen = true"
    >
      Tally Election
    </button>

    <div v-if="isOpen" class="modal fade show d-block" tabindex="-1" role="dialog" aria-modal="true" aria-labelledby="tally-election-modal-label">
      <div class="modal-dialog" role="document">
        <div class="modal-content">
          <form @submit.prevent="submit">
            <div class="modal-header">
              <h5 id="tally-election-modal-label" class="modal-title">Tally election?</h5>
              <button type="button" class="close" aria-label="Close" title="Close dialog" @click="closeModal">
                <span aria-hidden="true">&times;</span>
              </button>
            </div>
            <div class="modal-body">
              <div v-if="errors.length > 0" class="alert alert-danger" role="alert">
                <p v-for="error in errors" :key="error" class="mb-0">{{ error }}</p>
              </div>

              <p class="mb-3">You are about to tally this closed election.</p>

              <ul class="mb-3">
                <li>The tally will process the locked ballot set for this election.</li>
                <li>Successful tallying publishes the results and public audit records.</li>
              </ul>

              <div class="form-group mb-0">
                <label for="tally-confirm">Type the election name to confirm</label>
                <div class="text-muted small mb-2">
                  <strong>Election name:</strong> {{ bootstrap.electionName }}
                </div>
                <input
                  id="tally-confirm"
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
              <button type="button" class="btn btn-outline-secondary" title="Close dialog without tallying" @click="closeModal">Cancel</button>
              <button id="tally-submit" type="submit" class="btn btn-primary" :disabled="!canSubmit" :aria-disabled="!canSubmit" title="Tally the closed election">
                {{ isSubmitting ? "Tallying..." : "Tally Election" }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <div v-if="isOpen" class="modal-backdrop fade show"></div>
  </div>
</template>
