<script setup lang="ts">
import { computed, ref } from "vue";

import MailProgressModal from "../mail-progress/MailProgressModal.vue";
import { useMailProgress } from "../mail-progress/useMailProgress";
import type {
  ElectionStartAutomationBootstrap,
  ElectionStartPreview,
  ElectionStartPreviewResponse,
  ElectionStartResponse,
} from "./types";

const props = defineProps<{ bootstrap: ElectionStartAutomationBootstrap }>();
const enabled = ref(props.bootstrap.autoStartEnabled);
const busy = ref(false);
const error = ref("");

const preview = ref<ElectionStartPreview | null>(null);
const errors = ref<string[]>([]);

// Starting a large election queues one credential email per voter, which takes
// a while; the modal reports delivery progress so the operator can see that it
// is running instead of clicking Start again.
const starting = ref(false);
const { progress, percent, finished, failed, track } = useMailProgress(
  props.bootstrap.startProgressApiUrl,
  () => finish(),
);

const vacantSeats = computed(() => {
  const current = preview.value;
  return current === null ? 0 : Math.max(current.number_of_seats - current.candidate_count, 0);
});

function csrfToken(): string {
  return document.cookie.split(";").map((value) => value.trim()).find((value) => value.startsWith("csrftoken="))?.slice(10) || "";
}

async function post(url: string): Promise<ElectionStartResponse | null> {
  const response = await fetch(url, { method: "POST", credentials: "same-origin", headers: { Accept: "application/json", "X-CSRFToken": csrfToken() } });
  const payload = await response.json() as ElectionStartResponse;
  if (!response.ok || !payload.ok) {
    errors.value = payload.errors?.length ? payload.errors : ["Unable to update election automation."];
    return null;
  }
  return payload;
}

async function toggleAutoStart(): Promise<void> {
  busy.value = true;
  error.value = "";
  try {
    const response = await fetch(props.bootstrap.autoStartApiUrl, { method: "POST", credentials: "same-origin", headers: { Accept: "application/json", "X-CSRFToken": csrfToken() } });
    const payload = await response.json() as ElectionStartResponse;
    if (!response.ok || !payload.ok) {
      error.value = payload.errors?.[0] || "Unable to update election automation.";
      return;
    }
    enabled.value = Boolean(payload.election?.auto_start_enabled);
  } catch {
    error.value = "Unable to update election automation.";
  } finally {
    busy.value = false;
  }
}

async function openConfirmation(): Promise<void> {
  // Repeated clicks while a start is being confirmed or run must do nothing.
  if (busy.value || starting.value || preview.value !== null) {
    return;
  }
  busy.value = true;
  error.value = "";
  errors.value = [];
  try {
    const response = await fetch(props.bootstrap.startPreviewApiUrl, { credentials: "same-origin", headers: { Accept: "application/json" } });
    const payload = await response.json() as ElectionStartPreviewResponse;
    if (!response.ok || !payload.ok || !payload.start_preview) {
      error.value = "Unable to prepare the election start.";
      return;
    }
    preview.value = payload.start_preview;
  } catch {
    error.value = "Unable to prepare the election start.";
  } finally {
    busy.value = false;
  }
}

function cancelConfirmation(): void {
  if (starting.value) {
    return;
  }
  preview.value = null;
  errors.value = [];
}

async function startElection(): Promise<void> {
  if (busy.value || starting.value) {
    return;
  }
  busy.value = true;
  starting.value = true;
  errors.value = [];
  progress.value = null;

  try {
    const payload = await post(props.bootstrap.startApiUrl);
    if (payload === null) {
      starting.value = false;
      return;
    }
    if (payload.mail_progress == null) {
      // No delivery to follow (the election was already started elsewhere).
      window.location.reload();
      return;
    }
    track(payload.mail_progress);
  } catch {
    errors.value = ["Unable to start the election."];
    starting.value = false;
  } finally {
    busy.value = false;
  }
}

function finish(): void {
  window.location.reload();
}
</script>

<template>
  <div data-election-start-automation-vue-root>
    <p v-if="error" class="alert alert-danger" role="alert">{{ error }}</p>
    <div class="btn-group btn-block" role="group" aria-label="Election start actions">
      <button type="button" class="btn btn-success" :disabled="busy || starting" :aria-disabled="busy || starting" title="Start the election now" @click="openConfirmation">
        {{ starting ? "Starting..." : "Start Election" }}
      </button>
      <button type="button" class="btn btn-outline-success election-automation-toggle" :disabled="busy || starting" :aria-label="enabled ? 'Cancel automatic start' : 'Enable automatic start at the scheduled start time'" :aria-pressed="enabled" :title="enabled ? 'Automatic start is enabled. Click to cancel.' : 'Start automatically at the scheduled start time.'" @click="toggleAutoStart">
        <i :class="enabled ? 'fas fa-pause' : 'fas fa-play'" aria-hidden="true"></i>
      </button>
    </div>

    <div v-if="preview && !progress" id="start-election-modal" class="modal fade show d-block" tabindex="-1" role="dialog" aria-modal="true" aria-labelledby="start-election-modal-label">
      <div class="modal-dialog" role="document">
        <div class="modal-content">
          <div class="modal-header">
            <h5 id="start-election-modal-label" class="modal-title">Start election?</h5>
            <button type="button" class="close" aria-label="Close" title="Close dialog" @click="cancelConfirmation">
              <span aria-hidden="true">&times;</span>
            </button>
          </div>
          <div class="modal-body">
            <div v-if="errors.length > 0" class="alert alert-danger" role="alert">
              <p v-for="message in errors" :key="message" class="mb-0">{{ message }}</p>
            </div>

            <p class="mb-2">This will open the election and email voting credentials to all eligible voters.</p>
            <p v-if="vacantSeats > 0" class="text-danger mb-2">
              <strong>Warning:</strong>
              This election has {{ preview.number_of_seats }} seat{{ preview.number_of_seats === 1 ? "" : "s" }} but only
              {{ preview.candidate_count }} candidate{{ preview.candidate_count === 1 ? "" : "s" }}, so it will finish with
              {{ vacantSeats }} vacant seat{{ vacantSeats === 1 ? "" : "s" }}.
            </p>
            <p class="small mb-0">
              Eligible voters: <strong class="js-eligible-voters-count">{{ preview.eligible_voter_count }}</strong>
            </p>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-outline-secondary" :disabled="starting" title="Close dialog without starting" @click="cancelConfirmation">Cancel</button>
            <button id="start-election-submit" type="button" class="btn btn-success" :disabled="busy || starting" :aria-disabled="busy || starting" title="Start the election and send credentials" @click="startElection">
              Start election &amp; send credentials
            </button>
          </div>
        </div>
      </div>
    </div>

    <MailProgressModal
      v-if="progress"
      :progress="progress"
      :percent="percent"
      :finished="finished"
      :failed="failed"
      running-title="Starting election..."
      finished-title="Election started"
      description="The election is open. Voting credentials are being emailed to all eligible voters — this can take a few minutes."
      continue-label="Continue"
      unit-singular="credential email"
      unit-plural="credential emails"
      @continue="finish"
    />

    <div v-if="preview && !progress" class="modal-backdrop fade show"></div>
  </div>
</template>

<style scoped>
.election-automation-toggle {
  flex: 0 0 42px;
}
</style>
