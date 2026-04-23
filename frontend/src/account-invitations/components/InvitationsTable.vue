<script setup lang="ts">
import { computed, ref } from "vue";

import TableBase from "../../shared/components/TableBase.vue";
import type { AccountInvitationRow, AccountInvitationsBootstrap } from "../types";
import { formatDateTime } from "../types";

interface Props {
  bootstrap: AccountInvitationsBootstrap;
  rows: AccountInvitationRow[];
  count: number;
  currentPage: number;
  totalPages: number;
  isLoading: boolean;
  error: string | null;
  scope: "pending" | "accepted";
  buildPageHref?: (pageNumber: number) => string;
}

interface BulkSubmitPayload {
  action: string;
  selectedIds: string[];
  scope?: string;
}

const props = defineProps<Props>();

const emit = defineEmits<{
  "page-change": [page: number];
  "bulk-success": [];
  "open-action": [action: { invitationId: number; actionKind: "resend" | "dismiss" }];
}>();

const isProcessing = ref(false);
const bulkError = ref("");
const actionError = ref("");

const scopeLabel = computed(() => (props.scope === "accepted" ? "Accepted" : "Pending"));

const columns = computed(() => {
  if (props.scope === "pending") {
    return [
      { key: "email", label: "Email" },
      { key: "full-name", label: "Full name" },
      { key: "organization", label: "Organization" },
      { key: "note", label: "Note" },
      { key: "invited-by", label: "Invited by" },
      { key: "invited-at", label: "Invited at" },
      { key: "last-sent", label: "Last sent" },
      { key: "send-count", label: "Send count" },
      { key: "actions", label: "Actions", align: "right" as const },
    ];
  }

  return [
    { key: "email", label: "Email" },
    { key: "full-name", label: "Full name" },
    { key: "organization", label: "Organization" },
    { key: "note", label: "Note" },
    { key: "status", label: "Status" },
    { key: "accepted-at", label: "Accepted at" },
    { key: "actions", label: "Actions", align: "right" as const },
  ];
});

const bulkActions = computed(() => {
  if (props.scope === "pending") {
    return [
      { value: "resend", label: "Resend" },
      { value: "dismiss", label: "Dismiss" },
    ];
  }
  return [{ value: "dismiss", label: "Dismiss" }];
});

function rowId(row: unknown): string | number {
  return (row as AccountInvitationRow).invitation_id;
}

function invitationRow(row: unknown): AccountInvitationRow {
  return row as AccountInvitationRow;
}

function clearInlineErrors(): void {
  bulkError.value = "";
  actionError.value = "";
}

async function handleResend(invitationId: number): Promise<void> {
  clearInlineErrors();
  isProcessing.value = true;
  try {
    const url = props.bootstrap.resendApiUrl.replace(props.bootstrap.sentinelToken, String(invitationId));
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "X-CSRFToken": props.bootstrap.csrfToken,
        Accept: "application/json",
      },
      credentials: "same-origin",
    });

    if (!response.ok) {
      const data = (await response.json()) as { error?: string };
      actionError.value = data.error || "Failed to resend invitation.";
      return;
    }

    emit("open-action", { invitationId, actionKind: "resend" });
    emit("bulk-success");
  } catch {
    actionError.value = "Failed to resend invitation.";
  } finally {
    isProcessing.value = false;
  }
}

async function handleDismiss(invitationId: number): Promise<void> {
  clearInlineErrors();
  isProcessing.value = true;
  try {
    const url = props.bootstrap.dismissApiUrl.replace(props.bootstrap.sentinelToken, String(invitationId));
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "X-CSRFToken": props.bootstrap.csrfToken,
        Accept: "application/json",
      },
      credentials: "same-origin",
    });

    if (!response.ok) {
      const data = (await response.json()) as { error?: string };
      actionError.value = data.error || "Failed to dismiss invitation.";
      return;
    }

    emit("open-action", { invitationId, actionKind: "dismiss" });
    emit("bulk-success");
  } catch {
    actionError.value = "Failed to dismiss invitation.";
  } finally {
    isProcessing.value = false;
  }
}

async function handleBulkAction(payload: BulkSubmitPayload): Promise<void> {
  clearInlineErrors();

  if (!payload.action || payload.selectedIds.length === 0) {
    bulkError.value = "Please select an action and at least one invitation.";
    return;
  }

  isProcessing.value = true;
  try {
    const response = await fetch(props.bootstrap.bulkApiUrl, {
      method: "POST",
      headers: {
        "X-CSRFToken": props.bootstrap.csrfToken,
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      credentials: "same-origin",
      body: JSON.stringify({
        bulk_action: payload.action,
        bulk_scope: props.scope,
        selected: payload.selectedIds.map((id) => Number.parseInt(id, 10)).filter((id) => !Number.isNaN(id)),
      }),
    });

    if (!response.ok) {
      const data = (await response.json()) as { error?: string };
      bulkError.value = data.error || "Failed to perform bulk action.";
      return;
    }

    emit("bulk-success");
  } catch {
    bulkError.value = "Failed to perform bulk action.";
  } finally {
    isProcessing.value = false;
  }
}
</script>

<template>
  <TableBase
    :rows="rows"
    :count="count"
    :current-page="currentPage"
    :total-pages="totalPages"
    :is-loading="isLoading"
    :error="error"
    :loading-message="scope === 'pending' ? 'Loading pending invitations...' : 'Loading accepted invitations...'"
    :checkbox-class="`invitation-checkbox invitation-checkbox--${scope}`"
    :select-all-aria-label="`Select all ${scope} invitations`"
    :columns="columns"
    :empty-message="scope === 'accepted' ? 'No accepted invitations.' : 'No pending invitations.'"
    :get-row-id="rowId"
    :pagination-aria-label="`${scopeLabel} pagination`"
    :build-page-href="buildPageHref"
    :bulk-actions="bulkActions"
    bulk-action-placeholder="Bulk action…"
    bulk-submit-title="Apply selected action to checked invitations"
    bulk-validation-message="Please select an action and at least one invitation."
    :bulk-form-id="`bulk-invitations-${scope}-form`"
    :bulk-scope="scope"
    :bulk-error="bulkError"
    :bulk-submitting="isProcessing"
    :header-error="actionError"
    :page-size="bootstrap.pageSize"
    @page-change="emit('page-change', $event)"
    @bulk-submit="handleBulkAction"
  >
    <template #header-meta>
      <div class="text-muted">{{ scopeLabel }}: {{ count }}</div>
    </template>

    <template #row-cells="{ row }">
      <td>{{ invitationRow(row).email }}</td>
      <td>{{ invitationRow(row).full_name }}</td>
      <td>
        <span v-if="invitationRow(row).organization_id">
          <a :href="`/organization/${invitationRow(row).organization_id}/`">{{ invitationRow(row).organization_name }}</a>
        </span>
        <span v-else>-</span>
      </td>
      <td>{{ invitationRow(row).note }}</td>

      <template v-if="scope === 'pending'">
        <td>{{ invitationRow(row).invited_by_username }}</td>
        <td>{{ formatDateTime(invitationRow(row).invited_at) }}</td>
        <td>{{ invitationRow(row).last_sent_at ? formatDateTime(invitationRow(row).last_sent_at || null) : "-" }}</td>
        <td>{{ invitationRow(row).send_count }}</td>
      </template>

      <template v-else>
        <td>
          <template v-if="invitationRow(row).freeipa_matched_usernames && invitationRow(row).freeipa_matched_usernames.length > 1">
            Accepted (multiple matches)
            <div class="text-muted small">
              <template v-for="(username, idx) in invitationRow(row).freeipa_matched_usernames" :key="username">
                <a :href="`/user/${username}/`">{{ username }}</a><span v-if="idx < invitationRow(row).freeipa_matched_usernames.length - 1">, </span>
              </template>
            </div>
            <div v-if="invitationRow(row).accepted_username" class="text-muted small">
              as <a :href="`/user/${invitationRow(row).accepted_username}/`">{{ invitationRow(row).accepted_username }}</a>
            </div>
          </template>
          <template v-else>
            Accepted
            <div v-if="invitationRow(row).accepted_username" class="text-muted small">
              as <a :href="`/user/${invitationRow(row).accepted_username}/`">{{ invitationRow(row).accepted_username }}</a>
            </div>
          </template>
        </td>
        <td>{{ formatDateTime(invitationRow(row).accepted_at || null) }}</td>
      </template>

      <td class="text-right">
        <form v-if="scope === 'pending' && bootstrap.canResend" class="d-inline" @submit.prevent="handleResend(invitationRow(row).invitation_id)">
          <button
            type="submit"
            :disabled="isProcessing"
            class="btn btn-sm btn-outline-primary"
            title="Resend invitation email"
          >
            Resend
          </button>
        </form>
        <form v-if="bootstrap.canDismiss" class="d-inline" @submit.prevent="handleDismiss(invitationRow(row).invitation_id)">
          <button
            type="submit"
            :disabled="isProcessing"
            class="btn btn-sm btn-outline-secondary"
            title="Dismiss this invitation"
          >
            Dismiss
          </button>
        </form>
      </td>
    </template>

    <template #empty-state>{{ scope === 'accepted' ? 'No accepted invitations.' : 'No pending invitations.' }}</template>

  </TableBase>
</template>
