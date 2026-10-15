<script setup lang="ts">
import { ref } from "vue";

import type { MembershipRequestActionIntent, MembershipRequestDues } from "../types";
import MembershipRequestActionModal from "./MembershipRequestActionModal.vue";

interface MembershipActionSuccessEventDetail {
  actionUrl?: string;
  requestStatus?: string;
  actionKind?: string;
  payload?: unknown;
}

const props = defineProps<{
  requestId: number;
  requestStatus: string;
  membershipTypeName: string;
  requestTarget: string;
  approveUrl: string;
  approveOnHoldUrl: string;
  rejectUrl: string;
  rfiUrl: string;
  ignoreUrl: string;
  approveCustomEmailUrl?: string;
  rejectCustomEmailUrl?: string;
  rfiCustomEmailUrl?: string;
  canRequestInfo: boolean;
  showOnHoldApprove: boolean;
  csrfToken: string;
  // Only for approvers who may set dues.
  dues?: MembershipRequestDues | null;
}>();

const emit = defineEmits<{
  (event: "action-success", payload: MembershipActionSuccessEventDetail): void;
}>();

const activeAction = ref<MembershipRequestActionIntent | null>(null);

function openAction(
  actionKind: MembershipRequestActionIntent["actionKind"],
  actionUrl: string,
  customEmailUrl?: string,
): void {
  activeAction.value = {
    requestId: props.requestId,
    requestStatus: props.requestStatus,
    actionKind,
    actionUrl,
    requestTarget: props.requestTarget,
    membershipType: props.membershipTypeName,
    ...(customEmailUrl ? { customEmailUrl } : {}),
    ...(actionKind === "approve" && props.dues ? { dues: props.dues } : {}),
  };
}

function closeActionModal(): void {
  activeAction.value = null;
}

function onActionSuccess(payload: MembershipActionSuccessEventDetail): void {
  closeActionModal();
  emit("action-success", payload);
}
</script>

<template>
  <div class="membership-request-actions membership-request-actions--detail">
    <button
      v-if="requestStatus === 'pending'"
      type="button"
      class="btn btn-sm btn-success"
      title="Approve this request"
      aria-label="Approve"
      @click="openAction('approve', approveUrl, approveCustomEmailUrl)"
    >Approve</button>

    <button
      v-if="requestStatus === 'on_hold' && showOnHoldApprove"
      type="button"
      class="btn btn-sm btn-success"
      title="Approve this on-hold request with committee override"
      aria-label="Approve"
      @click="openAction('approve_on_hold', approveOnHoldUrl)"
    >Approve</button>

    <button
      v-if="requestStatus === 'pending' || requestStatus === 'on_hold'"
      type="button"
      class="btn btn-sm btn-danger"
      title="Reject this request"
      aria-label="Reject"
      @click="openAction('reject', rejectUrl, rejectCustomEmailUrl)"
    >Reject</button>

    <button
      v-if="requestStatus === 'pending' && canRequestInfo"
      type="button"
      class="btn btn-sm btn-outline-primary"
      title="Request information and put on hold"
      aria-label="Request for Information"
      @click="openAction('rfi', rfiUrl, rfiCustomEmailUrl)"
    >RFI</button>

    <button
      v-if="requestStatus === 'pending' || requestStatus === 'on_hold'"
      type="button"
      class="btn btn-sm btn-outline-secondary"
      title="Ignore this request"
      aria-label="Ignore"
      @click="openAction('ignore', ignoreUrl)"
    >Ignore</button>

    <MembershipRequestActionModal
      :action="activeAction"
      :csrf-token="csrfToken"
      @close="closeActionModal"
      @success="onActionSuccess"
    />
  </div>
</template>
