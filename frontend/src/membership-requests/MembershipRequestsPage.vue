<script setup lang="ts">
import { computed, onMounted } from "vue";

import { useMembershipRequestsTable } from "./composables/useMembershipRequestsTable";
import PendingRequestsTable from "./components/PendingRequestsTable.vue";
import OnHoldRequestsTable from "./components/OnHoldRequestsTable.vue";
import type { MembershipRequestsBootstrap } from "./types";

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
  pageSize: 50,
  orderName: "requested_at",
  initialPage: readPageParam("pending_page"),
  initialFilter: new URLSearchParams(window.location.search).get("filter") || "all",
});

const onHoldTable = useMembershipRequestsTable({
  url: props.bootstrap.onHoldApiUrl,
  pageSize: 10,
  orderName: "on_hold_at",
  initialPage: readPageParam("on_hold_page"),
});

onMounted(async () => {
  await Promise.all([pendingTable.load(), onHoldTable.load()]);
  syncUrl(pendingTable.currentPage.value, onHoldTable.currentPage.value, pendingTable.selectedFilter.value);
});

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

const pendingTotalPages = computed(() => Math.max(1, Math.ceil(pendingTable.totalRows.value / 50)));
const onHoldTotalPages = computed(() => Math.max(1, Math.ceil(onHoldTable.totalRows.value / 10)));
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
      :is-loading="pendingTable.isLoading.value"
      :error="pendingTable.error.value"
      @filter-change="onPendingFilterChange"
      @page-change="onPendingPageChange"
    />
    <OnHoldRequestsTable
      :bootstrap="bootstrap"
      :rows="onHoldTable.rows.value"
      :count="onHoldTable.totalRows.value"
      :current-page="onHoldTable.currentPage.value"
      :total-pages="onHoldTotalPages"
      :is-loading="onHoldTable.isLoading.value"
      :error="onHoldTable.error.value"
      @page-change="onOnHoldPageChange"
    />
  </div>
</template>