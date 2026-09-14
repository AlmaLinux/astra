<script setup lang="ts">
import { computed } from "vue";

import type { MailProgress } from "./types";

const props = defineProps<{
  progress: MailProgress;
  runningTitle: string;
  finishedTitle: string;
  description: string;
  percent: number;
  finished: boolean;
  failed: boolean;
  continueLabel: string;
  unitSingular?: string;
  unitPlural?: string;
}>();

defineEmits<{ continue: [] }>();

const unit = computed(() =>
  props.progress.total === 1 ? (props.unitSingular ?? "email") : (props.unitPlural ?? "emails"),
);
</script>

<template>
  <div>
    <div class="modal fade show d-block" tabindex="-1" role="dialog" aria-modal="true" aria-labelledby="election-mail-progress-modal-label">
      <div class="modal-dialog" role="document">
        <div class="modal-content">
          <div class="modal-header">
            <h5 id="election-mail-progress-modal-label" class="modal-title">
              {{ finished ? finishedTitle : runningTitle }}
            </h5>
          </div>
          <div class="modal-body">
            <p class="mb-2">{{ description }}</p>

            <div class="progress mb-2" style="height: 1.25rem">
              <div
                class="progress-bar"
                :class="{ 'progress-bar-striped progress-bar-animated': !finished, 'bg-success': finished && !failed, 'bg-danger': failed }"
                role="progressbar"
                :style="{ width: `${percent}%` }"
                :aria-valuenow="percent"
                aria-valuemin="0"
                aria-valuemax="100"
              >
                {{ percent }}%
              </div>
            </div>

            <p class="small mb-0" data-mail-progress-counts>
              {{ progress.processed }} of {{ progress.total }} {{ unit }} sent
              <span v-if="progress.skipped > 0">&middot; {{ progress.skipped }} skipped (no email address)</span>
              <span v-if="progress.failures > 0" class="text-danger">&middot; {{ progress.failures }} failed</span>
            </p>

            <p v-if="progress.message" class="alert alert-warning mt-3 mb-0" role="alert">{{ progress.message }}</p>
          </div>
          <div class="modal-footer">
            <button
              data-mail-progress-continue
              type="button"
              class="btn btn-primary"
              :disabled="!finished"
              :aria-disabled="!finished"
              @click="$emit('continue')"
            >
              {{ finished ? continueLabel : "Sending..." }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <div class="modal-backdrop fade show"></div>
  </div>
</template>
