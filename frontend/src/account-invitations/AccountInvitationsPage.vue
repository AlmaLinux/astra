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
      :build-page-href="acceptedPageHref"
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
      :build-page-href="pendingPageHref"
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
 * Read pagination page parameter from URL.
 */
function readPageParam(name: string): number {
  const params = new URLSearchParams(window.location.search);
  const rawValue = Number.parseInt(params.get(name) || "1", 10);
  return Number.isNaN(rawValue) || rawValue < 1 ? 1 : rawValue;
}

function buildPageHref(paramName: string, pageNumber: number): string {
  const params = new URLSearchParams(window.location.search);
  if (pageNumber <= 1) {
    params.delete(paramName);
  } else {
    params.set(paramName, String(pageNumber));
  }
  const query = params.toString();
  return `${window.location.pathname}${query ? `?${query}` : ""}`;
}

/**
 * Load both tables on mount.
 */
onMounted(async () => {
  await Promise.all([
    pendingTable.load(readPageParam("pending_page")),
    acceptedTable.load(readPageParam("accepted_page")),
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
 * Build pagination URL for pending invitations.
 */
function pendingPageHref(pageNumber: number): string {
  return buildPageHref("pending_page", pageNumber);
}

/**
 * Build pagination URL for accepted invitations.
 */
function acceptedPageHref(pageNumber: number): string {
  return buildPageHref("accepted_page", pageNumber);
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
  const params = new URLSearchParams(window.location.search);

  if (pendingTable.currentPage.value <= 1) {
    params.delete("pending_page");
  } else {
    params.set("pending_page", String(pendingTable.currentPage.value));
  }

  if (acceptedTable.currentPage.value <= 1) {
    params.delete("accepted_page");
  } else {
    params.set("accepted_page", String(acceptedTable.currentPage.value));
  }

  const query = params.toString();
  const newUrl = `${window.location.pathname}${query ? `?${query}` : ""}`;
  window.history.replaceState(window.history.state, "", newUrl);
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
