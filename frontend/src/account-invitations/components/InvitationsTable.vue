<template>
  <div class="card" :class="{ 'mb-4': scope === 'pending' }">
    <div class="card-header">
      <div class="d-flex align-items-center justify-content-between flex-wrap" style="gap: 0.5rem;">
        <form
          @submit.prevent="handleBulkAction"
          class="form-inline"
          :id="`bulk-invitations-${scope}-form`"
          data-bulk-table-form
          :data-bulk-select-all-id="`select-all-invitations-${scope}`"
          :data-bulk-checkbox-selector="`.invitation-checkbox--${scope}`"
          :data-bulk-apply-id="`bulk-apply-${scope}`"
        >
          <input v-if="scope === 'accepted'" type="hidden" name="bulk_scope" value="accepted">
          <div class="input-group input-group-sm">
            <select v-model="selectedAction" name="bulk_action" class="custom-select custom-select-sm" aria-label="Bulk action">
              <option value="">Bulk action…</option>
              <option v-if="scope === 'pending'" value="resend">Resend</option>
              <option value="dismiss">Dismiss</option>
            </select>
            <div class="input-group-append">
              <button
                type="submit"
                :disabled="!selectedAction || isProcessing || selectedIds.length === 0"
                class="btn btn-default"
                :id="`bulk-apply-${scope}`"
                title="Apply selected action to checked invitations"
              >
                Apply
              </button>
            </div>
          </div>
        </form>

        <div class="text-muted">{{ scopeLabel }}: {{ count }}</div>
      </div>
    </div>

    <div class="card-body p-0">
      <div v-if="error" class="alert alert-danger m-3" role="alert">
        {{ error }}
      </div>

      <div class="table-responsive">
        <table class="table table-striped mb-0">
          <thead>
            <tr>
              <th style="width: 40px;" class="text-center">
                <input
                  type="checkbox"
                  :id="`select-all-invitations-${scope}`"
                  :checked="isAllSelected"
                  @change="toggleSelectAll"
                  :aria-label="`Select all ${scope} invitations`"
                >
              </th>
              <th>Email</th>
              <th>Full name</th>
              <th>Organization</th>
              <th>Note</th>
              <th v-if="scope === 'pending'">Invited by</th>
              <th v-if="scope === 'pending'">Invited at</th>
              <th v-if="scope === 'pending'">Last sent</th>
              <th v-if="scope === 'pending'">Send count</th>
              <th v-if="scope === 'accepted'">Status</th>
              <th v-if="scope === 'accepted'">Accepted at</th>
              <th class="text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.invitation_id">
              <td class="text-center align-top">
                <input
                  type="checkbox"
                  :class="`invitation-checkbox invitation-checkbox--${scope}`"
                  name="selected"
                  :value="row.invitation_id"
                  :form="`bulk-invitations-${scope}-form`"
                  v-model.number="selectedIds"
                  aria-label="Select invitation"
                >
              </td>
              <td>{{ row.email }}</td>
              <td>{{ row.full_name }}</td>
              <td>
                <span v-if="row.organization_id">
                  <a :href="`/organization/${row.organization_id}/`">{{ row.organization_name }}</a>
                </span>
                <span v-else>-</span>
              </td>
              <td>{{ row.note }}</td>
              <td v-if="scope === 'pending'">{{ row.invited_by_username }}</td>
              <td v-if="scope === 'pending'">{{ formatDateTime(row.invited_at) }}</td>
              <td v-if="scope === 'pending'">{{ row.last_sent_at ? formatDateTime(row.last_sent_at) : "-" }}</td>
              <td v-if="scope === 'pending'">{{ row.send_count }}</td>
              <td v-if="scope === 'accepted'">
                <template v-if="row.freeipa_matched_usernames && row.freeipa_matched_usernames.length > 1">
                  Accepted (multiple matches)
                  <div class="text-muted small">
                    <template v-for="(username, idx) in row.freeipa_matched_usernames" :key="username">
                      <a :href="`/user/${username}/`">{{ username }}</a><span v-if="idx < row.freeipa_matched_usernames.length - 1">, </span>
                    </template>
                  </div>
                  <div v-if="row.accepted_username" class="text-muted small">
                    as <a :href="`/user/${row.accepted_username}/`">{{ row.accepted_username }}</a>
                  </div>
                </template>
                <template v-else>
                  Accepted
                  <div v-if="row.accepted_username" class="text-muted small">
                    as <a :href="`/user/${row.accepted_username}/`">{{ row.accepted_username }}</a>
                  </div>
                </template>
              </td>
              <td v-if="scope === 'accepted'">{{ formatDateTime(row.accepted_at) }}</td>
              <td class="text-right">
                <form v-if="scope === 'pending' && bootstrap.canResend" class="d-inline" @submit.prevent="handleResend(row.invitation_id)">
                  <button
                    type="submit"
                    :disabled="isProcessing"
                    class="btn btn-sm btn-outline-primary"
                    title="Resend invitation email"
                  >
                    Resend
                  </button>
                </form>
                <form v-if="bootstrap.canDismiss" class="d-inline" @submit.prevent="handleDismiss(row.invitation_id)">
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
            </tr>
            <tr v-if="rows.length === 0">
              <td :colspan="colSpan" class="text-muted text-center py-3">{{ emptyMessage }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="border-top p-2 clearfix">
        <div class="float-left text-muted small">
          <template v-if="count > 0">
            Showing {{ startIndex }}–{{ endIndex }} of {{ count }}
          </template>
        </div>

        <ul v-if="totalPages > 1" class="pagination pagination-sm m-0 float-right">
          <li class="page-item" :class="{ disabled: currentPage === 1 }">
            <a
              class="page-link"
              href="#"
              aria-label="Previous"
              @click.prevent="currentPage > 1 && $emit('page-change', currentPage - 1)"
            >&laquo;</a>
          </li>

          <li v-if="showFirst" class="page-item">
            <a class="page-link" href="#" @click.prevent="$emit('page-change', 1)">1</a>
          </li>
          <li v-if="showFirst" class="page-item disabled"><span class="page-link">…</span></li>

          <li
            v-for="page in pageNumbers"
            :key="page"
            class="page-item"
            :class="{ active: page === currentPage }"
          >
            <a class="page-link" href="#" @click.prevent="$emit('page-change', page)">{{ page }}</a>
          </li>

          <li v-if="showLast" class="page-item disabled"><span class="page-link">…</span></li>
          <li v-if="showLast" class="page-item">
            <a class="page-link" href="#" @click.prevent="$emit('page-change', totalPages)">{{ totalPages }}</a>
          </li>

          <li class="page-item" :class="{ disabled: currentPage === totalPages }">
            <a
              class="page-link"
              href="#"
              aria-label="Next"
              @click.prevent="currentPage < totalPages && $emit('page-change', currentPage + 1)"
            >&raquo;</a>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from "vue";
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
  title: string;
  scope: "pending" | "accepted";
}

const props = defineProps<Props>();

const emit = defineEmits<{
  "page-change": [page: number];
  "bulk-success": [];
  "open-action": [action: any];
}>();

const selectedIds = ref<number[]>([]);
const selectedAction = ref("");
const isProcessing = ref(false);

const isAllSelected = computed(() => {
  return props.rows.length > 0 && selectedIds.value.length === props.rows.length;
});

const scopeLabel = computed(() => (props.scope === "accepted" ? "Accepted" : "Pending"));
const emptyMessage = computed(() =>
  props.scope === "accepted" ? "No accepted invitations." : "No pending invitations."
);

const pageNumbers = computed(() => {
  const pages: number[] = [];
  const startPage = Math.max(1, props.currentPage - 2);
  const endPage = Math.min(props.totalPages, props.currentPage + 2);

  for (let i = startPage; i <= endPage; i++) {
    pages.push(i);
  }
  return pages;
});

const showFirst = computed(() => {
  const startPage = Math.max(1, props.currentPage - 2);
  return startPage > 1;
});

const showLast = computed(() => {
  const endPage = Math.min(props.totalPages, props.currentPage + 2);
  return endPage < props.totalPages;
});

const startIndex = computed(() => {
  if (props.count === 0) return 0;
  return (props.currentPage - 1) * (props.bootstrap.pageSize || 50) + 1;
});

const endIndex = computed(() => {
  if (props.count === 0) return 0;
  return Math.min(props.currentPage * (props.bootstrap.pageSize || 50), props.count);
});

const colSpan = computed(() => {
  return props.scope === "pending" ? 10 : 8;
});

function toggleSelectAll(event: Event) {
  const target = event.target as HTMLInputElement;
  if (target.checked) {
    selectedIds.value = props.rows.map((row) => row.invitation_id);
  } else {
    selectedIds.value = [];
  }
}

async function handleResend(invitationId: number): Promise<void> {
  isProcessing.value = true;
  try {
    const url = props.bootstrap.resendApiUrl.replace("123456789", String(invitationId));
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "X-CSRFToken": props.bootstrap.csrfToken,
        "Accept": "application/json",
      },
      credentials: "same-origin",
    });

    if (response.ok) {
      emit("bulk-success");
    } else {
      const data = await response.json();
      alert(data.error || "Failed to resend invitation");
    }
  } catch (err) {
    alert("Error: " + (err instanceof Error ? err.message : "Unknown error"));
  } finally {
    isProcessing.value = false;
  }
}

async function handleDismiss(invitationId: number): Promise<void> {
  isProcessing.value = true;
  try {
    const url = props.bootstrap.dismissApiUrl.replace("123456789", String(invitationId));
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "X-CSRFToken": props.bootstrap.csrfToken,
        "Accept": "application/json",
      },
      credentials: "same-origin",
    });

    if (response.ok) {
      emit("bulk-success");
    } else {
      const data = await response.json();
      alert(data.error || "Failed to dismiss invitation");
    }
  } catch (err) {
    alert("Error: " + (err instanceof Error ? err.message : "Unknown error"));
  } finally {
    isProcessing.value = false;
  }
}

async function handleBulkAction(): Promise<void> {
  if (!selectedAction.value || selectedIds.value.length === 0) {
    alert("Please select an action and at least one invitation");
    return;
  }

  isProcessing.value = true;
  try {
    const response = await fetch(props.bootstrap.bulkApiUrl, {
      method: "POST",
      headers: {
        "X-CSRFToken": props.bootstrap.csrfToken,
        "Accept": "application/json",
        "Content-Type": "application/json",
      },
      credentials: "same-origin",
      body: JSON.stringify({
        bulk_action: selectedAction.value,
        bulk_scope: props.scope,
        selected: selectedIds.value,
      }),
    });

    if (response.ok) {
      selectedIds.value = [];
      selectedAction.value = "";
      emit("bulk-success");
    } else {
      const data = await response.json();
      alert(data.error || "Failed to perform bulk action");
    }
  } catch (err) {
    alert("Error: " + (err instanceof Error ? err.message : "Unknown error"));
  } finally {
    isProcessing.value = false;
  }
}
</script>
