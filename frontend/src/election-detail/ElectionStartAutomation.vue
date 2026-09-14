<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";

import type {
  ElectionStartAutomationBootstrap,
  ElectionStartPreview,
  ElectionStartProgress,
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
const progress = ref<ElectionStartProgress | null>(null);
const POLL_INTERVAL_MS = 1000;
let pollTimer: number | undefined;

const vacantSeats = computed(() => {
  const current = preview.value;
  return current === null ? 0 : Math.max(current.number_of_seats - current.candidate_count, 0);
});

const percent = computed(() => {
  const current = progress.value;
  if (current === null || current.total <= 0) {
    return 0;
  }
  return Math.min(100, Math.round((current.processed / current.total) * 100));
});

const finished = computed(() => progress.value !== null && progress.value.state !== "running");
const failed = computed(() => progress.value !== null && (progress.value.state === "failed" || progress.value.state === "stalled"));

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
    if (payload.start_progress == null) {
      // No delivery to follow (the election was already started elsewhere).
      window.location.reload();
      return;
    }
    progress.value = payload.start_progress;
    schedulePoll();
  } catch {
    errors.value = ["Unable to start the election."];
    starting.value = false;
  } finally {
    busy.value = false;
  }
}

function schedulePoll(): void {
  if (finished.value) {
    return;
  }
  pollTimer = window.setTimeout(pollProgress, POLL_INTERVAL_MS);
}

async function pollProgress(): Promise<void> {
  try {
    const response = await fetch(props.bootstrap.startProgressApiUrl, { credentials: "same-origin", headers: { Accept: "application/json" } });
    const payload = await response.json() as ElectionStartResponse;
    if (response.ok) {
      if (payload.start_progress == null) {
        // The progress record expired; the delivery is no longer observable.
        finish();
        return;
      }
      progress.value = payload.start_progress;
    }
  } catch {
    // A dropped poll is not fatal: keep polling, and the stalled state on the
    // server side ends the wait if delivery really did stop.
  }
  schedulePoll();
}

function finish(): void {
  window.location.reload();
}

onBeforeUnmount(() => {
  window.clearTimeout(pollTimer);
});
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

    <div v-if="progress" class="modal fade show d-block" tabindex="-1" role="dialog" aria-modal="true" aria-labelledby="election-start-progress-modal-label">
      <div class="modal-dialog" role="document">
        <div class="modal-content">
          <div class="modal-header">
            <h5 id="election-start-progress-modal-label" class="modal-title">
              {{ finished ? "Election started" : "Starting election..." }}
            </h5>
          </div>
          <div class="modal-body">
            <p class="mb-2">
              The election is open. Voting credentials are being emailed to all eligible voters &mdash; this can take a few minutes.
            </p>

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

            <p class="small mb-0" data-election-start-progress-counts>
              {{ progress.processed }} of {{ progress.total }} credential email{{ progress.total === 1 ? "" : "s" }} sent
              <span v-if="progress.skipped > 0">&middot; {{ progress.skipped }} skipped (no email address)</span>
              <span v-if="progress.failures > 0" class="text-danger">&middot; {{ progress.failures }} failed</span>
            </p>

            <p v-if="progress.message" class="alert alert-warning mt-3 mb-0" role="alert">{{ progress.message }}</p>
          </div>
          <div class="modal-footer">
            <button id="start-election-continue" type="button" class="btn btn-primary" :disabled="!finished" :aria-disabled="!finished" title="Go to the election" @click="finish">
              {{ finished ? "Continue" : "Sending..." }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="preview || progress" class="modal-backdrop fade show"></div>
  </div>
</template>

<style scoped>
.election-automation-toggle {
  flex: 0 0 42px;
}
</style>
