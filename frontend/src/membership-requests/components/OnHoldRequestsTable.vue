<script setup lang="ts">
import { computed, ref } from "vue";

import type { MembershipRequestRow, MembershipRequestsBootstrap } from "../types";
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
}>();

const bulkAction = ref("");
const selectedIds = ref<number[]>([]);

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
      loading-message="Loading on-hold requests..."
      @page-change="onPageChange"
    >
      <template #header>
        <div class="d-flex align-items-center justify-content-between flex-wrap" style="gap: 0.5rem;">
          <form method="post" :action="bootstrap.bulkUrl || '/membership/requests/bulk/'" class="form-inline">
            <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken || ''">
            <input type="hidden" name="next" :value="nextUrl">
            <input type="hidden" name="bulk_scope" value="on_hold">
            <div class="input-group input-group-sm">
              <select v-model="bulkAction" name="bulk_action" class="custom-select custom-select-sm" aria-label="Bulk action">
                <option value="">Bulk action…</option>
                <option value="reject">Reject</option>
                <option value="ignore">Ignore</option>
              </select>
              <div class="input-group-append">
                <button type="submit" class="btn btn-default" title="Apply selected action to checked requests" :disabled="selectedIds.length === 0 || !bulkAction">Apply</button>
              </div>
            </div>
            <input v-for="requestId in selectedIds" :key="requestId" type="hidden" name="selected" :value="String(requestId)">
          </form>
          <div class="text-muted">On hold: {{ count }}</div>
        </div>
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