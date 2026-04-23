<script setup lang="ts">
import { computed } from "vue";

import type { MembershipRequestActionIntent, MembershipRequestRow, MembershipRequestsBootstrap } from "../types";
import { membershipRequestTargetContext, replaceTemplateToken } from "../types";

const props = defineProps<{
  row: MembershipRequestRow;
  bootstrap: MembershipRequestsBootstrap;
}>();

const emit = defineEmits<{
  (event: "open-action", value: MembershipRequestActionIntent): void;
}>();

const requester = computed(() => {
  return membershipRequestTargetContext(props.row.target);
});

function actionUrl(template: string): string {
  return replaceTemplateToken(template, props.bootstrap.requestIdSentinel, props.row.request_id);
}

function openAction(actionKind: MembershipRequestActionIntent["actionKind"], template: string): void {
  emit("open-action", {
    requestId: props.row.request_id,
    requestStatus: props.row.status,
    actionKind,
    actionUrl: actionUrl(template),
    requestTarget: requester.value,
    membershipType: props.row.membership_type.name,
  });
}
</script>

<template>
  <div class="membership-request-actions membership-request-actions--list d-flex justify-content-end flex-wrap">
    <button
      v-if="row.status === 'pending'"
      type="button"
      class="btn btn-sm btn-success"
      title="Approve this request"
      aria-label="Approve"
      @click="openAction('approve', bootstrap.approveTemplate)"
    >Approve</button>
    <button
      v-if="row.status === 'on_hold'"
      type="button"
      class="btn btn-sm btn-success"
      title="Approve this on-hold request with committee override"
      aria-label="Approve"
      @click="openAction('approve_on_hold', bootstrap.approveOnHoldTemplate)"
    >Approve</button>
    <button
      type="button"
      class="btn btn-sm btn-danger"
      title="Reject this request"
      aria-label="Reject"
      @click="openAction('reject', bootstrap.rejectTemplate)"
    >Reject</button>
    <button
      v-if="row.status === 'pending' && bootstrap.canRequestInfo"
      type="button"
      class="btn btn-sm btn-outline-primary"
      title="Request information and put on hold"
      aria-label="Request for Information"
      @click="openAction('rfi', bootstrap.requestInfoTemplate)"
    >RFI</button>
    <button
      type="button"
      class="btn btn-sm btn-outline-secondary"
      title="Ignore this request"
      aria-label="Ignore"
      @click="openAction('ignore', bootstrap.ignoreTemplate)"
    >Ignore</button>
  </div>
</template>