<script setup lang="ts">
import { computed } from "vue";

import type { MembershipRequestActionIntent, MembershipRequestRow, MembershipRequestsBootstrap, PendingFilterOption } from "../types";
import { buildMembershipRequestsRouteUrl, readMembershipRequestsRouteState } from "../types";
import RequestsTable from "./RequestsTable.vue";
import MembershipRequestsFilterBar from "./MembershipRequestsFilterBar.vue";

const props = defineProps<{
  bootstrap: MembershipRequestsBootstrap;
  rows: MembershipRequestRow[];
  count: number;
  filterOptions: PendingFilterOption[];
  selectedFilter: string;
  currentPage: number;
  totalPages: number;
  isLoading: boolean;
  error: string;
}>();

const emit = defineEmits<{
  (event: "filter-change", value: string): void;
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
];

function pendingPageHref(pageNumber: number): string {
  return buildMembershipRequestsRouteUrl({
    ...routeState.value,
    filter: props.selectedFilter,
    pendingPage: pageNumber,
  });
}

function onFilterChange(value: string): void {
  emit("filter-change", value);
}

function onPageChange(pageNumber: number): void {
  emit("page-change", pageNumber);
}
</script>

<template>
  <RequestsTable
    :bootstrap="bootstrap"
    :rows="rows"
    :count="count"
    :current-page="currentPage"
    :total-pages="totalPages"
    :is-loading="isLoading"
    :error="error"
    checkbox-class="request-checkbox--pending"
    pagination-aria-label="Pending pagination"
    :build-page-href="pendingPageHref"
    :columns="columns"
    :colspan="5"
    :bulk-actions="[
      { value: 'accept', label: 'Accept' },
      { value: 'reject', label: 'Reject' },
      { value: 'ignore', label: 'Ignore' },
    ]"
    bulk-form-id="bulk-action-form"
    loading-message="Loading pending requests..."
    @page-change="onPageChange"
    @bulk-success="emit('bulk-success', $event)"
    @open-action="emit('open-action', $event)"
  >
    <template #header-tools>
      <MembershipRequestsFilterBar
        :selected-filter="selectedFilter"
        :options="filterOptions"
        @change="onFilterChange"
      />
    </template>
    <template #header-meta>
      <div class="text-muted">Pending: {{ count }}</div>
    </template>

    <template #row-extra-columns="{ row }">
      <td class="align-top">
        {{ row.membership_type.name }}
        <div v-if="row.is_renewal" class="mt-1">
          <span class="badge badge-primary">Renewal</span>
        </div>
        <details v-if="row.responses.length" class="mt-2" open>
          <summary class="small text-muted">Request responses</summary>
          <div class="mt-2">
            <div v-for="response in row.responses" :key="response.question">
              <div class="small text-muted font-weight-bold">{{ response.question }}</div>
              <div class="small" style="white-space: pre-wrap;" v-html="response.answer_html"></div>
            </div>
          </div>
        </details>
      </td>
    </template>

    <template #empty-state>
      <template v-if="selectedFilter !== 'all'">
        No requests match this filter.
        <a :href="bootstrap.clearFilterUrl" class="ml-1">Clear filter</a>
      </template>
      <template v-else>No pending requests.</template>
    </template>
  </RequestsTable>
</template>