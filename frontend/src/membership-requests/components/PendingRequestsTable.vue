<script setup lang="ts">
import { computed, ref } from "vue";

import type { MembershipRequestRow, MembershipRequestsBootstrap, PendingFilterOption } from "../types";
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
];

function pendingPageHref(pageNumber: number): string {
  return buildMembershipRequestsRouteUrl({
    ...routeState.value,
    filter: props.selectedFilter,
    pendingPage: pageNumber,
  });
}

function onFilterChange(value: string): void {
  selectedIds.value = [];
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
    loading-message="Loading pending requests..."
    @page-change="onPageChange"
  >
    <template #header>
      <div class="d-flex align-items-center justify-content-between flex-wrap" style="gap: 0.5rem;">
        <div class="d-flex align-items-center flex-wrap" style="gap: 0.5rem;">
          <form
            id="bulk-action-form"
            method="post"
            :action="bootstrap.bulkUrl || '/membership/requests/bulk/'"
            class="form-inline"
          >
            <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken || ''">
            <input type="hidden" name="next" :value="nextUrl">
            <div class="input-group input-group-sm">
              <select v-model="bulkAction" name="bulk_action" class="custom-select custom-select-sm" aria-label="Bulk action">
                <option value="">Bulk action…</option>
                <option value="accept">Accept</option>
                <option value="reject">Reject</option>
                <option value="ignore">Ignore</option>
              </select>
              <div class="input-group-append">
                <button type="submit" class="btn btn-default" title="Apply selected action to checked requests" :disabled="selectedIds.length === 0 || !bulkAction">Apply</button>
              </div>
            </div>
            <input v-for="requestId in selectedIds" :key="requestId" type="hidden" name="selected" :value="String(requestId)">
          </form>

          <MembershipRequestsFilterBar
            :selected-filter="selectedFilter"
            :options="filterOptions"
            @change="onFilterChange"
          />
        </div>

        <div class="text-muted">Pending: {{ count }}</div>
      </div>
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