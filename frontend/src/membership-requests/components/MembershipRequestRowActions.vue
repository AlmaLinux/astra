<script setup lang="ts">
import { computed } from "vue";

import type { MembershipRequestRow, MembershipRequestsBootstrap } from "../types";
import { membershipRequestTargetContext, replaceTemplateToken } from "../types";

const props = defineProps<{
  row: MembershipRequestRow;
  bootstrap: MembershipRequestsBootstrap;
}>();

const requester = computed(() => {
  return membershipRequestTargetContext(props.row.target);
});

function actionUrl(template: string): string {
  return replaceTemplateToken(template, props.bootstrap.requestIdSentinel, props.row.request_id);
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
      data-toggle="modal"
      data-target="#shared-approve-modal"
      :data-action-url="actionUrl(bootstrap.approveTemplate)"
      :data-modal-title="`Approve ${row.membership_type.name} request`"
      :data-request-id="String(row.request_id)"
      :data-request-target="requester"
      :data-membership-type="row.membership_type.name"
      :data-body-prefix="`Approve ${row.membership_type.name} request from`"
      :data-body-emphasis="requester"
      data-body-suffix="?"
    >Approve</button>
    <button
      v-if="row.status === 'on_hold'"
      type="button"
      class="btn btn-sm btn-success"
      title="Approve this on-hold request with committee override"
      aria-label="Approve"
      data-toggle="modal"
      data-target="#shared-approve-on-hold-modal"
      :data-action-url="actionUrl(bootstrap.approveOnHoldTemplate)"
      :data-modal-title="`Approve ${row.membership_type.name} request`"
      :data-request-id="String(row.request_id)"
      :data-request-target="requester"
      :data-membership-type="row.membership_type.name"
    >Approve</button>
    <button
      type="button"
      class="btn btn-sm btn-danger"
      title="Reject this request"
      aria-label="Reject"
      data-toggle="modal"
      data-target="#shared-reject-modal"
      :data-action-url="actionUrl(bootstrap.rejectTemplate)"
      :data-modal-title="`Reject ${row.membership_type.name} request`"
      :data-request-id="String(row.request_id)"
      :data-request-target="requester"
      :data-membership-type="row.membership_type.name"
    >Reject</button>
    <button
      v-if="row.status === 'pending' && bootstrap.canRequestInfo"
      type="button"
      class="btn btn-sm btn-outline-primary"
      title="Request information and put on hold"
      aria-label="Request for Information"
      data-toggle="modal"
      data-target="#shared-rfi-modal"
      :data-action-url="actionUrl(bootstrap.requestInfoTemplate)"
      :data-modal-title="`Request information for ${row.membership_type.name} request`"
      :data-request-id="String(row.request_id)"
      :data-request-target="requester"
      :data-membership-type="row.membership_type.name"
    >RFI</button>
    <button
      type="button"
      class="btn btn-sm btn-outline-secondary"
      title="Ignore this request"
      aria-label="Ignore"
      data-toggle="modal"
      data-target="#shared-ignore-modal"
      :data-action-url="actionUrl(bootstrap.ignoreTemplate)"
      :data-modal-title="`Ignore ${row.membership_type.name} request`"
      :data-request-id="String(row.request_id)"
      :data-request-target="requester"
      :data-membership-type="row.membership_type.name"
      :data-body-prefix="`Ignore ${row.membership_type.name} request from`"
      :data-body-emphasis="requester"
      data-body-suffix="? This does not approve the membership, nor does it notify the user."
    >Ignore</button>
  </div>
</template>