<template>
  <div class="account-invitations-page">
    <InvitationsTable
      v-if="showAccepted"
      :bootstrap="bootstrap"
      :rows="acceptedTable.rows.value"
      :count="acceptedTable.totalRows.value"
      :current-page="acceptedTable.currentPage.value"
      :total-pages="acceptedTable.totalPages.value"
      :is-loading="acceptedTable.isLoading.value"
      :error="acceptedTable.error.value"
      title="Accepted Invitations"
      scope="accepted"
      @page-change="onAcceptedPageChange"
      @bulk-success="onBulkSuccess('accepted')"
      @open-action="onOpenAction"
    />

    <!-- Pending Invitations Section (SECOND) -->
    <h3 v-if="showPending" class="mt-4 mb-2">Waiting for account creation</h3>
    <InvitationsTable
      v-if="showPending"
      :bootstrap="bootstrap"
      :rows="pendingTable.rows.value"
      :count="pendingTable.totalRows.value"
      :current-page="pendingTable.currentPage.value"
      :total-pages="pendingTable.totalPages.value"
      :is-loading="pendingTable.isLoading.value"
      :error="pendingTable.error.value"
      title="Pending Invitations"
      scope="pending"
      @page-change="onPendingPageChange"
      @bulk-success="onBulkSuccess('pending')"
      @open-action="onOpenAction"
    />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, computed } from "vue";
import InvitationsTable from "./components/InvitationsTable.vue";
import { useAccountInvitationsTable } from "./composables/useAccountInvitationsTable";
import type { AccountInvitationsBootstrap } from "./types";

interface Props {
  bootstrap: AccountInvitationsBootstrap;
}

const props = defineProps<Props>();

// Initialize table composables
const pendingTable = useAccountInvitationsTable({
  bootstrap: props.bootstrap,
  apiUrl: props.bootstrap.pendingApiUrl,
  scope: "pending",
});

const acceptedTable = useAccountInvitationsTable({
  bootstrap: props.bootstrap,
  apiUrl: props.bootstrap.acceptedApiUrl,
  scope: "accepted",
});

const isRefreshing = ref(false);

// Visibility flags
const showPending = computed(() => props.bootstrap.canManageInvitations);
const showAccepted = computed(() => props.bootstrap.canManageInvitations);

/**
 * Load both tables on mount.
 */
onMounted(async () => {
  await Promise.all([
    pendingTable.load(1),
    acceptedTable.load(1),
  ]);
});

/**
 * Handle page change for pending invitations.
 */
async function onPendingPageChange(page: number): Promise<void> {
  await pendingTable.reloadForPageNum(page);
  syncUrl();
}

/**
 * Handle page change for accepted invitations.
 */
async function onAcceptedPageChange(page: number): Promise<void> {
  await acceptedTable.reloadForPageNum(page);
  syncUrl();
}

/**
 * Handle refresh button click.
 */
async function handleRefresh(): Promise<void> {
  isRefreshing.value = true;
  try {
    const response = await fetch(props.bootstrap.refreshApiUrl, {
      method: "POST",
      headers: {
        "X-CSRFToken": props.bootstrap.csrfToken,
        "Accept": "application/json",
      },
      credentials: "same-origin",
    });

    if (response.ok) {
      // Reload both tables after refresh
      await Promise.all([
        pendingTable.reloadForPage(),
        acceptedTable.reloadForPage(),
      ]);
    } else {
      console.error("Refresh failed:", response.status);
    }
  } catch (err) {
    console.error("Refresh error:", err);
  } finally {
    isRefreshing.value = false;
  }
}

/**
 * Handle successful bulk action.
 */
async function onBulkSuccess(scope: string): Promise<void> {
  if (scope === "pending") {
    await pendingTable.reloadForPage();
  } else if (scope === "accepted") {
    await acceptedTable.reloadForPage();
  }
}

/**
 * Handle action modal open (for future implementation).
 */
function onOpenAction(action: any): void {
  // TODO: Implement action modal for single invite actions
  console.log("Action:", action);
}

/**
 * Sync current page state to URL (for future implementation).
 */
function syncUrl(): void {
  // TODO: Update URL params with current pagination state
  // window.history.replaceState({}, "", newUrl);
}

/**
 * Expose refresh handler for header button.
 */
defineExpose({
  handleRefresh,
});
</script>

<style scoped lang="css">
.account-invitations-page {
  width: 100%;
}
</style>
