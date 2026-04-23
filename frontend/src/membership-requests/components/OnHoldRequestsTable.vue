<script setup lang="ts">
import { computed } from "vue";

import type { MembershipRequestActionIntent, MembershipRequestRow, MembershipRequestsBootstrap } from "../types";
import { buildMembershipRequestsRouteUrl, formatLegacyDateTime, formatRelativeAgo, readMembershipRequestsRouteState } from "../types";
import RequestsTable from "./RequestsTable.vue";

const props = defineProps<{
  bootstrap: MembershipRequestsBootstrap;
  rows: MembershipRequestRow[];
  count: number;
  currentPage: number;
  totalPages: number;
  isLoading: boolean;
  error: string;
}>();

const emit = defineEmits<{
  (event: "page-change", value: number): void;
  (event: "bulk-success", payload: { scope: "pending" | "on_hold" }): void;
  (event: "open-action", payload: MembershipRequestActionIntent): void;
}>();

const nextUrl = computed(() => {
  if (props.bootstrap.nextUrl) {
    return props.bootstrap.nextUrl;
  }
  if (typeof window === "undefined") {
    return "/membership/requests/";
  }
  return `${window.location.pathname}${window.location.search}`;
});

const routeState = computed(() => {
  const currentUrl = typeof window === "undefined" ? nextUrl.value : `${window.location.pathname}${window.location.search}`;
  return readMembershipRequestsRouteState(currentUrl);
});

const columns = [
  { key: "type", label: "Type" },
  { key: "on-hold-since", label: "On hold since", width: "1%", noWrap: true },
];

function onHoldPageHref(pageNumber: number): string {
  return buildMembershipRequestsRouteUrl({
    ...routeState.value,
    onHoldPage: pageNumber,
  });
}

function onPageChange(pageNumber: number): void {
  emit("page-change", pageNumber);
}
</script>

<template>
  <div>
    <h3 class="mt-4 mb-2">Waiting for requester response</h3>
    <RequestsTable
      :bootstrap="bootstrap"
      :rows="rows"
      :count="count"
      :current-page="currentPage"
      :total-pages="totalPages"
      :is-loading="isLoading"
      :error="error"
      checkbox-class="request-checkbox--on-hold"
      pagination-aria-label="On-hold pagination"
      :build-page-href="onHoldPageHref"
      :columns="columns"
      :colspan="6"
      :bulk-actions="[
        { value: 'reject', label: 'Reject' },
        { value: 'ignore', label: 'Ignore' },
      ]"
      bulk-scope="on_hold"
      loading-message="Loading on-hold requests..."
      @page-change="onPageChange"
      @bulk-success="emit('bulk-success', $event)"
      @open-action="emit('open-action', $event)"
    >
      <template #header-meta>
        <div class="text-muted">On hold: {{ count }}</div>
      </template>

      <template #row-extra-columns="{ row }">
        <td class="align-top">{{ row.membership_type.name }}</td>
        <td class="align-top">
          <div>{{ formatLegacyDateTime(row.on_hold_since) }}</div>
          <div class="small text-muted mt-1">{{ formatRelativeAgo(row.on_hold_since) }}</div>
        </td>
      </template>
    </RequestsTable>
  </div>
</template>