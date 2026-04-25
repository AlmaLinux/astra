<script setup lang="ts">
import { computed, ref } from "vue";

import type { ElectionConcludeActionBootstrap } from "./types";

const props = defineProps<{
  bootstrap: ElectionConcludeActionBootstrap;
}>();

const isOpen = ref(false);
const confirmValue = ref("");
const skipTally = ref(false);
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
    const response = await fetch(props.bootstrap.concludeApiUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": readCsrfToken(),
      },
      body: JSON.stringify({
        confirm: confirmValue.value,
        skip_tally: skipTally.value,
      }),
    });

    if (!response.ok) {
      const payload = (await response.json().catch(() => ({ errors: ["Unable to conclude the election right now."] }))) as {
        errors?: string[];
      };
      errors.value = payload.errors && payload.errors.length > 0 ? payload.errors : ["Unable to conclude the election right now."];
      return;
    }

    window.location.reload();
  } catch {
    errors.value = ["Unable to conclude the election right now."];
  } finally {
    isSubmitting.value = false;
  }
}
</script>

<template>
  <div data-election-conclude-action-vue-root>
    <button
      type="button"
      class="btn btn-danger btn-block"
      title="Conclude the election and stop voting"
      @click="isOpen = true"
    >
      Conclude Election
    </button>

    <div v-if="isOpen" class="modal fade show d-block" tabindex="-1" role="dialog" aria-modal="true" aria-labelledby="conclude-election-modal-label">
      <div class="modal-dialog" role="document">
        <div class="modal-content">
          <form @submit.prevent="submit">
            <div class="modal-header">
              <h5 id="conclude-election-modal-label" class="modal-title">Conclude election?</h5>
              <button type="button" class="close" aria-label="Close" title="Close dialog" @click="closeModal">
                <span aria-hidden="true">&times;</span>
              </button>
            </div>
            <div class="modal-body">
              <div v-if="errors.length > 0" class="alert alert-danger" role="alert">
                <p v-for="error in errors" :key="error" class="mb-0">{{ error }}</p>
              </div>

              <div v-if="bootstrap.quorumWarning" class="alert alert-warning mb-3" role="alert">
                <strong>Quorum not met:</strong> {{ bootstrap.quorumWarning.replace(/^Quorum not met:\s*/i, "") }}
              </div>
              <p class="mb-3">You are about to close this election and end voting.</p>

              <ul class="mb-3">
                <li>Voting ends and no further ballots can be submitted.</li>
                <li><strong>Irreversible:</strong> Voting credentials are anonymized and election-related emails are scrubbed.</li>
                <li>If you tally now, results and public audit artifacts will be published.</li>
              </ul>

              <div class="custom-control custom-checkbox">
                <input id="conclude-skip-tally" v-model="skipTally" type="checkbox" class="custom-control-input">
                <label class="custom-control-label" for="conclude-skip-tally">Close election, but do not tally votes</label>
              </div>

              <hr>

              <div class="form-group mb-0">
                <label for="conclude-confirm">Type the election name to confirm</label>
                <div class="text-muted small mb-2">
                  <strong>Election name:</strong> {{ bootstrap.electionName }}
                </div>
                <input
                  id="conclude-confirm"
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
              <button type="button" class="btn btn-outline-secondary" title="Close dialog without concluding" @click="closeModal">Cancel</button>
              <button id="conclude-submit" type="submit" class="btn btn-danger" :disabled="!canSubmit" :aria-disabled="!canSubmit" title="Conclude the election and stop voting">
                {{ isSubmitting ? "Concluding..." : "Conclude Election" }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <div v-if="isOpen" class="modal-backdrop fade show"></div>
  </div>
</template>