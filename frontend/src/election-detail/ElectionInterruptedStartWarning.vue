<script setup lang="ts">
import { computed, ref } from "vue";

import MailProgressModal from "../mail-progress/MailProgressModal.vue";
import { useMailProgress } from "../mail-progress/useMailProgress";
import type { MailProgress } from "../mail-progress/types";
import { readCsrfToken } from "../shared/csrf";
import type { ElectionInterruptedStartBootstrap } from "./types";

const props = defineProps<{ bootstrap: ElectionInterruptedStartBootstrap }>();

// A start issues credentials, emails every voter, then records and announces
// itself. Anything that kills the run part way leaves the last steps undone.
const missingCount = ref(props.bootstrap.missingCount);
const startRecorded = ref(props.bootstrap.startRecorded);
const busy = ref(false);
const error = ref("");

const { progress, percent, finished, failed, track, reset: resetProgress } = useMailProgress(
  props.bootstrap.progressApiUrl,
  () => finish(),
);

const voterLabel = computed(() => (missingCount.value === 1 ? "voter" : "voters"));
const outstanding = computed(() => missingCount.value > 0 || !startRecorded.value);

async function completeStart(): Promise<void> {
  if (busy.value || progress.value !== null) {
    return;
  }
  busy.value = true;
  error.value = "";

  try {
    const response = await fetch(props.bootstrap.completeApiUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json", "X-CSRFToken": readCsrfToken() },
    });
    const payload = await response.json() as { ok: boolean; errors?: string[]; mail_progress?: MailProgress | null };
    if (!response.ok || !payload.ok) {
      error.value = payload.errors?.[0] || "Unable to finish the interrupted start.";
      return;
    }
    if (payload.mail_progress == null) {
      finish();
      return;
    }
    track(payload.mail_progress);
  } catch {
    error.value = "Unable to finish the interrupted start.";
  } finally {
    busy.value = false;
  }
}

function finish(): void {
  const current = progress.value;
  if (current !== null) {
    // Whoever the run could not reach is still outstanding.
    missingCount.value = current.skipped + current.failures;
  }
  // Recording and announcing the election is part of the same run.
  startRecorded.value = true;
  resetProgress();
}
</script>

<template>
  <div data-election-interrupted-start-vue-root>
    <div v-if="!outstanding" class="alert alert-success" role="status">
      This election's start is complete: every eligible voter has their voting credentials, and the
      election has been recorded in the audit log and announced.
    </div>

    <div v-else class="alert alert-warning" role="alert">
      <h5 class="mb-2">
        <i class="fas fa-exclamation-triangle mr-1" aria-hidden="true"></i>
        This election's start did not finish
      </h5>
      <p class="mb-2">Voting is open, but the run that started it was interrupted before it could:</p>
      <ul class="mb-2">
        <li v-if="missingCount > 0">
          email <strong>{{ missingCount }}</strong> eligible {{ voterLabel }} their voting credentials
          &mdash; {{ missingCount === 1 ? "that voter" : "those voters" }} cannot vote yet
        </li>
        <li v-if="!startRecorded">
          record the start in the public audit log, so the election was never announced either
        </li>
      </ul>
      <p class="small mb-3">
        Finishing picks up where the run stopped: only the
        <template v-if="missingCount > 0">{{ missingCount }} affected {{ voterLabel }} are emailed, and nobody who already received their credentials is emailed again</template>
        <template v-else>outstanding steps are run</template>.
      </p>

      <p v-if="error" class="text-danger mb-2">{{ error }}</p>

      <button
        data-election-interrupted-start-complete
        type="button"
        class="btn btn-primary"
        :disabled="busy || progress !== null"
        :aria-disabled="busy || progress !== null"
        @click="completeStart"
      >
        <i class="fas fa-play mr-1" aria-hidden="true"></i>
        Finish the interrupted start
      </button>
    </div>

    <MailProgressModal
      v-if="progress"
      :progress="progress"
      :percent="percent"
      :finished="finished"
      :failed="failed"
      running-title="Finishing the interrupted start..."
      finished-title="Start finished"
      description="Picking up where the interrupted run stopped — only the voters it never reached are being emailed."
      continue-label="Done"
      unit-singular="credential email"
      unit-plural="credential emails"
      @continue="finish"
    />
  </div>
</template>
