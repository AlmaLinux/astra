<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { MembershipRequestActionIntent } from "../types";

interface MembershipActionSuccessEventDetail {
  actionUrl?: string;
  requestStatus?: string;
  actionKind?: string;
  payload?: unknown;
}

const REJECT_PRESETS: Array<{ label: string; value: string }> = [
  {
    label: "RFI unanswered",
    value: "We were unable to complete the approval process because we did not receive the additional information requested during our review.",
  },
  {
    label: "Embargoed country",
    value: "This decision is due to legal requirements that currently prevent the AlmaLinux OS Foundation from approving applications from certain countries.",
  },
];

const RFI_PRESETS: Array<{ label: string; value: string }> = [
  {
    label: "More details",
    value: "Please provide additional details about your involvement with the AlmaLinux community, including any contributions, participation, or other relevant activities.",
  },
  {
    label: "Incomplete Mirror PR",
    value: "Please review the status of your mirror pull request and address any outstanding issues so we can continue our review.",
  },
];

const props = defineProps<{
  action: MembershipRequestActionIntent | null;
  csrfToken: string;
}>();

const emit = defineEmits<{
  (event: "close"): void;
  (event: "success", payload: MembershipActionSuccessEventDetail): void;
}>();

const message = ref("");
const selectedPreset = ref("");
const error = ref("");
const isSubmitting = ref(false);

watch(
  () => props.action,
  () => {
    message.value = "";
    selectedPreset.value = "";
    error.value = "";
    isSubmitting.value = false;
  },
);

const isOpen = computed(() => props.action !== null);

const modalTitle = computed(() => {
  if (!props.action) {
    return "";
  }

  if (props.action.actionKind === "approve") {
    return `Approve ${props.action.membershipType} request`;
  }
  if (props.action.actionKind === "approve_on_hold") {
    return `Approve ${props.action.membershipType} request`;
  }
  if (props.action.actionKind === "reject") {
    return `Reject ${props.action.membershipType} request`;
  }
  if (props.action.actionKind === "rfi") {
    return `Request information for ${props.action.membershipType} request`;
  }
  return `Ignore ${props.action.membershipType} request`;
});

const submitLabel = computed(() => {
  if (!props.action) {
    return "Submit";
  }
  if (props.action.actionKind === "rfi") {
    return "Send RFI";
  }
  if (props.action.actionKind === "reject") {
    return "Reject";
  }
  if (props.action.actionKind === "ignore") {
    return "Ignore";
  }
  return "Approve";
});

const submitClass = computed(() => {
  if (!props.action) {
    return "btn btn-primary";
  }
  if (props.action.actionKind === "reject") {
    return "btn btn-danger";
  }
  if (props.action.actionKind === "rfi") {
    return "btn btn-primary";
  }
  if (props.action.actionKind === "ignore") {
    return "btn btn-outline-secondary";
  }
  return "btn btn-success";
});

const presets = computed(() => {
  if (!props.action) {
    return [];
  }
  if (props.action.actionKind === "reject") {
    return REJECT_PRESETS;
  }
  if (props.action.actionKind === "rfi") {
    return RFI_PRESETS;
  }
  return [];
});

const fieldName = computed(() => {
  if (!props.action) {
    return "";
  }
  if (props.action.actionKind === "approve_on_hold") {
    return "justification";
  }
  if (props.action.actionKind === "reject") {
    return "reason";
  }
  if (props.action.actionKind === "rfi") {
    return "rfi_message";
  }
  return "";
});

const fieldLabel = computed(() => {
  if (!props.action) {
    return "";
  }
  if (props.action.actionKind === "approve_on_hold") {
    return "Committee override justification:";
  }
  if (props.action.actionKind === "reject") {
    return "Rejection reason (optional):";
  }
  if (props.action.actionKind === "rfi") {
    return "RFI message:";
  }
  return "";
});

const helpText = computed(() => {
  if (!props.action) {
    return "";
  }
  if (props.action.actionKind === "approve_on_hold") {
    return "Required for this committee override approval. This note is stored in the membership request audit trail.";
  }
  if (props.action.actionKind === "reject") {
    return "If filled in, this text will be sent in the rejection email.";
  }
  if (props.action.actionKind === "rfi") {
    return "This text will be sent in the Request for Information email and the request will be put on hold.";
  }
  return "";
});

const requiresMessage = computed(() => {
  if (!props.action) {
    return false;
  }
  return props.action.actionKind === "approve_on_hold" || props.action.actionKind === "rfi";
});

const supportsTextarea = computed(() => {
  if (!props.action) {
    return false;
  }
  return props.action.actionKind === "approve_on_hold" || props.action.actionKind === "reject" || props.action.actionKind === "rfi";
});

const bodyText = computed(() => {
  if (!props.action) {
    return "";
  }
  if (props.action.actionKind === "approve") {
    return `Approve ${props.action.membershipType} request from ${props.action.requestTarget}?`;
  }
  if (props.action.actionKind === "ignore") {
    return `Ignore ${props.action.membershipType} request from ${props.action.requestTarget}? This does not approve the membership, nor does it notify the user.`;
  }
  return "";
});

function onPresetChange(): void {
  message.value = selectedPreset.value;
}

function closeModal(): void {
  if (isSubmitting.value) {
    return;
  }
  emit("close");
}

async function submitAction(): Promise<void> {
  if (!props.action || isSubmitting.value) {
    return;
  }

  if (requiresMessage.value && !message.value.trim()) {
    error.value = "This field is required.";
    return;
  }

  error.value = "";
  isSubmitting.value = true;

  try {
    const formData = new FormData();
    const trimmedMessage = message.value.trim();

    if (fieldName.value && (trimmedMessage || requiresMessage.value)) {
      formData.append(fieldName.value, trimmedMessage);
    }

    const headers: Record<string, string> = {
      Accept: "application/json",
    };
    if (props.csrfToken) {
      headers["X-CSRFToken"] = props.csrfToken;
    }

    const response = await fetch(props.action.actionUrl, {
      method: "POST",
      headers,
      body: formData,
      credentials: "same-origin",
    });

    const payload = (await response.json().catch(() => ({}))) as { ok?: boolean; error?: string };
    if (!response.ok || payload.ok === false) {
      error.value = payload.error || "Failed to process request.";
      return;
    }

    emit("success", {
      actionUrl: props.action.actionUrl,
      requestStatus: props.action.requestStatus,
      actionKind: props.action.actionKind,
      payload,
    });
    emit("close");
  } catch {
    error.value = "Failed to process request.";
  } finally {
    isSubmitting.value = false;
  }
}
</script>

<template>
  <div v-if="isOpen">
    <div class="modal d-block" tabindex="-1" role="dialog" aria-modal="true">
      <div class="modal-dialog" role="document" style="text-align: left;">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title">{{ modalTitle }}</h5>
            <button type="button" class="close" aria-label="Close" :disabled="isSubmitting" @click="closeModal">
              <span aria-hidden="true">&times;</span>
            </button>
          </div>
          <div class="modal-body">
            <div class="alert alert-light border py-2 px-3 small" role="note">
              <div>Confirming action for:</div>
              <div>Request #<strong>{{ action?.requestId }}</strong></div>
              <div>Requested for <strong>{{ action?.requestTarget }}</strong></div>
              <div>Membership type <strong>{{ action?.membershipType }}</strong></div>
            </div>

            <div v-if="bodyText" class="mb-3">{{ bodyText }}</div>

            <div v-if="supportsTextarea" class="mb-3">
              <div v-if="presets.length" class="form-group">
                <label for="membership-request-action-preset">Quick responses</label>
                <select
                  id="membership-request-action-preset"
                  v-model="selectedPreset"
                  class="form-control"
                  @change="onPresetChange"
                >
                  <option value="">Choose a preset (optional)</option>
                  <option v-for="preset in presets" :key="preset.label" :value="preset.value">{{ preset.label }}</option>
                </select>
                <div class="text-muted small mt-1">Selecting a preset will copy it into the box below. You can still edit it.</div>
              </div>

              <div class="form-group mb-0">
                <label for="membership-request-action-text">{{ fieldLabel }}</label>
                <textarea
                  id="membership-request-action-text"
                  v-model="message"
                  class="form-control"
                  rows="4"
                  :required="requiresMessage"
                />
              </div>
              <div v-if="helpText" class="text-muted small mt-2">{{ helpText }}</div>
            </div>

            <div v-if="error" class="alert alert-danger py-2 px-3" role="alert">{{ error }}</div>

            <div class="d-flex justify-content-between">
              <button type="button" class="btn btn-secondary" :disabled="isSubmitting" @click="closeModal">Cancel</button>
              <button type="button" :class="submitClass" :disabled="isSubmitting" @click="submitAction">{{ submitLabel }}</button>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="modal-backdrop fade show" @click="closeModal"></div>
  </div>
</template>
