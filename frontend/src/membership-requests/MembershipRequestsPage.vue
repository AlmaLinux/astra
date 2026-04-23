<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { useMembershipRequestsTable } from "./composables/useMembershipRequestsTable";
import MembershipRequestActionModal from "./components/MembershipRequestActionModal.vue";
import PendingRequestsTable from "./components/PendingRequestsTable.vue";
import OnHoldRequestsTable from "./components/OnHoldRequestsTable.vue";
import type { MembershipRequestActionIntent, MembershipRequestsBootstrap } from "./types";

type TableScope = "pending" | "on_hold";

interface MembershipActionSuccessEventDetail {
  actionUrl?: string;
  requestStatus?: string;
  actionKind?: string;
  payload?: unknown;
}

const props = defineProps<{
  bootstrap: MembershipRequestsBootstrap;
}>();

function readPageParam(name: string): number {
  const params = new URLSearchParams(window.location.search);
  const rawValue = Number.parseInt(params.get(name) || "1", 10);
  return Number.isNaN(rawValue) || rawValue < 1 ? 1 : rawValue;
}

function syncUrl(pendingPage: number, onHoldPage: number, filter: string): void {
  const params = new URLSearchParams(window.location.search);
  if (filter === "all") {
    params.delete("filter");
  } else {
    params.set("filter", filter);
  }
  if (pendingPage <= 1) {
    params.delete("pending_page");
  } else {
    params.set("pending_page", String(pendingPage));
  }
  if (onHoldPage <= 1) {
    params.delete("on_hold_page");
  } else {
    params.set("on_hold_page", String(onHoldPage));
  }
  const query = params.toString();
  const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}`;
  window.history.replaceState(null, "", nextUrl);
}

const pendingTable = useMembershipRequestsTable({
  url: props.bootstrap.pendingApiUrl,
  pageSize: props.bootstrap.pendingPageSize,
  orderName: "requested_at",
  initialPage: readPageParam("pending_page"),
  initialFilter: new URLSearchParams(window.location.search).get("filter") || "all",
});

const onHoldTable = useMembershipRequestsTable({
  url: props.bootstrap.onHoldApiUrl,
  pageSize: props.bootstrap.onHoldPageSize,
  orderName: "on_hold_at",
  initialPage: readPageParam("on_hold_page"),
});

onMounted(async () => {
  await Promise.all([pendingTable.load(), onHoldTable.load()]);
  syncUrl(pendingTable.currentPage.value, onHoldTable.currentPage.value, pendingTable.selectedFilter.value);
});

const activeAction = ref<MembershipRequestActionIntent | null>(null);

async function refreshTables(): Promise<void> {
  await Promise.all([pendingTable.load(), onHoldTable.load()]);
  syncUrl(pendingTable.currentPage.value, onHoldTable.currentPage.value, pendingTable.selectedFilter.value);
}

async function refreshPendingTable(): Promise<void> {
  await pendingTable.load();
  syncUrl(pendingTable.currentPage.value, onHoldTable.currentPage.value, pendingTable.selectedFilter.value);
}

async function refreshOnHoldTable(): Promise<void> {
  await onHoldTable.load();
  syncUrl(pendingTable.currentPage.value, onHoldTable.currentPage.value, pendingTable.selectedFilter.value);
}

async function refreshByScope(scope: TableScope): Promise<void> {
  if (scope === "pending") {
    await refreshPendingTable();
    return;
  }
  await refreshOnHoldTable();
}

async function refreshByScopes(scopes: TableScope[]): Promise<void> {
  const uniqueScopes = new Set(scopes);
  if (uniqueScopes.size === 0 || uniqueScopes.size > 1) {
    await refreshTables();
    return;
  }
  const [scope] = Array.from(uniqueScopes);
  await refreshByScope(scope);
}

function scopesForMembershipAction(detail: MembershipActionSuccessEventDetail): TableScope[] {
  const actionKind = String(detail.actionKind || "").toLowerCase();
  const requestStatus = String(detail.requestStatus || "").toLowerCase();

  if (!actionKind || !requestStatus) {
    return ["pending", "on_hold"];
  }

  if (actionKind === "rfi" && requestStatus === "pending") {
    return ["pending", "on_hold"];
  }

  if (requestStatus === "pending") {
    return ["pending"];
  }

  if (requestStatus === "on_hold") {
    return ["on_hold"];
  }

  return ["pending", "on_hold"];
}

function onOpenAction(payload: MembershipRequestActionIntent): void {
  activeAction.value = payload;
}

function closeActionModal(): void {
  activeAction.value = null;
}

async function onActionModalSuccess(payload: MembershipActionSuccessEventDetail): Promise<void> {
  await refreshByScopes(scopesForMembershipAction(payload));
}

async function onPendingFilterChange(filter: string): Promise<void> {
  await pendingTable.reloadForFilter(filter);
  syncUrl(pendingTable.currentPage.value, onHoldTable.currentPage.value, pendingTable.selectedFilter.value);
}

async function onPendingPageChange(page: number): Promise<void> {
  await pendingTable.reloadForPage(page);
  syncUrl(pendingTable.currentPage.value, onHoldTable.currentPage.value, pendingTable.selectedFilter.value);
}

async function onOnHoldPageChange(page: number): Promise<void> {
  await onHoldTable.reloadForPage(page);
  syncUrl(pendingTable.currentPage.value, onHoldTable.currentPage.value, pendingTable.selectedFilter.value);
}

async function onBulkSuccess(payload: { scope: TableScope }): Promise<void> {
  await refreshByScope(payload.scope);
}

const pendingTotalPages = computed(() => Math.max(1, Math.ceil(pendingTable.totalRows.value / props.bootstrap.pendingPageSize)));
const onHoldTotalPages = computed(() => Math.max(1, Math.ceil(onHoldTable.totalRows.value / props.bootstrap.onHoldPageSize)));
</script>

<template>
  <div data-membership-requests-vue-root>
    <PendingRequestsTable
      :bootstrap="bootstrap"
      :rows="pendingTable.rows.value"
      :count="pendingTable.totalRows.value"
      :filter-options="pendingTable.filterOptions.value"
      :selected-filter="pendingTable.selectedFilter.value"
      :current-page="pendingTable.currentPage.value"
      :total-pages="pendingTotalPages"
      :page-size="bootstrap.pendingPageSize"
      :is-loading="pendingTable.isLoading.value"
      :error="pendingTable.error.value"
      @filter-change="onPendingFilterChange"
      @page-change="onPendingPageChange"
      @bulk-success="onBulkSuccess"
      @open-action="onOpenAction"
    />
    <h3 class="mt-4 mb-2">Waiting for requester response</h3>
    <OnHoldRequestsTable
      :bootstrap="bootstrap"
      :rows="onHoldTable.rows.value"
      :count="onHoldTable.totalRows.value"
      :current-page="onHoldTable.currentPage.value"
      :total-pages="onHoldTotalPages"
      :page-size="bootstrap.onHoldPageSize"
      :is-loading="onHoldTable.isLoading.value"
      :error="onHoldTable.error.value"
      @page-change="onOnHoldPageChange"
      @bulk-success="onBulkSuccess"
      @open-action="onOpenAction"
    />
    <MembershipRequestActionModal
      :action="activeAction"
      :csrf-token="bootstrap.csrfToken || ''"
      @close="closeActionModal"
      @success="onActionModalSuccess"
    />
  </div>
</template>