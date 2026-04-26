<script setup lang="ts">
import { computed, ref } from "vue";

import TableBase from "../../shared/components/TableBase.vue";
import { fillUrlTemplate } from "../../shared/urlTemplates";
import type { AuditLogRow } from "../types";

const props = defineProps<{
  rows: AuditLogRow[];
  count: number;
  currentPage: number;
  totalPages: number;
  pageSize: number;
  q: string;
  isLoading: boolean;
  error: string;
  userProfileUrlTemplate: string;
  organizationDetailUrlTemplate: string;
  membershipRequestDetailUrlTemplate: string;
  buildPageHref: (pageNumber: number) => string;
}>();

const emit = defineEmits<{
  (event: "page-change", value: number): void;
  (event: "search", value: string): void;
}>();

const searchText = ref(props.q);
const hasSearchValue = computed(() => searchText.value.length > 0);

const columns = computed(() => [
  { key: "when", label: "When", width: "1%", noWrap: true },
  { key: "who", label: "Who", width: "1%", noWrap: true },
  { key: "target", label: "Target", width: "1%", noWrap: true },
  { key: "membership", label: "Membership", width: "1%", noWrap: true },
  { key: "action", label: "Action" },
  { key: "expires", label: "Expires", width: "1%", noWrap: true },
]);

function rowId(row: unknown): string | number {
  return (row as AuditLogRow).log_id;
}

function asRow(row: unknown): AuditLogRow {
  return row as AuditLogRow;
}

function submitSearch(): void {
  emit("search", searchText.value.trim());
}

function clearSearch(): void {
  searchText.value = "";
  submitSearch();
}

function targetHref(row: AuditLogRow): string {
  if (row.target.deleted) {
    return "";
  }
  if (row.target.kind === "user") {
    return fillUrlTemplate(props.userProfileUrlTemplate, "__username__", row.target.label);
  }
  if (row.target.id === null) {
    return "";
  }
  return fillUrlTemplate(props.organizationDetailUrlTemplate, "__organization_id__", row.target.id);
}

function requestHref(row: AuditLogRow): string {
  if (!row.request) {
    return "";
  }
  return fillUrlTemplate(props.membershipRequestDetailUrlTemplate, "__request_id__", row.request.request_id);
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
    loading-message="Loading membership audit log..."
    checkbox-class="audit-log-checkbox"
    :columns="columns"
    :page-size="pageSize"
    :get-row-id="rowId"
    pagination-aria-label="Membership audit log pagination"
    :build-page-href="buildPageHref"
    empty-message="No audit log entries."
    :show-selection="false"
    @page-change="emit('page-change', $event)"
  >
    <template #header-meta>
      <form data-audit-log-search-form class="input-group input-group-sm" style="width: 260px;" @submit.prevent="submitSearch">
        <input
          v-model="searchText"
          type="text"
          name="q"
          class="form-control float-right"
          placeholder="Search"
          aria-label="Search membership audit log"
        >
        <div class="input-group-append">
          <button
            v-if="hasSearchValue"
            type="button"
            class="btn btn-default"
            aria-label="Clear search"
            title="Clear search filter"
            @click="clearSearch"
          >
            <i class="fas fa-times" />
          </button>
          <button type="submit" class="btn btn-default" aria-label="Search" title="Search audit log">
            <i class="fas fa-search" />
          </button>
        </div>
      </form>
    </template>

    <template #row-cells="{ row }">
      <td class="text-muted text-nowrap" style="width: 1%;">
        <div v-if="asRow(row).request">
          <a :href="requestHref(asRow(row))">Request #{{ asRow(row).request?.request_id }}</a>
        </div>
        <div>{{ asRow(row).created_at_display }}</div>
      </td>
      <td>{{ asRow(row).actor_username }}</td>
      <td>
        <template v-if="targetHref(asRow(row))">
          <a :href="targetHref(asRow(row))">{{ asRow(row).target.label }}</a>
        </template>
        <template v-else>
          <span>{{ asRow(row).target.label }}</span>
          <span v-if="asRow(row).target.deleted" class="text-muted"> (deleted)</span>
        </template>
      </td>
      <td>{{ asRow(row).membership_name }}</td>
      <td>
        {{ asRow(row).action_display }}
        <details v-if="asRow(row).request" class="mt-1">
          <summary class="small text-muted">Request responses</summary>
          <div class="mt-2">
            <div v-for="responseItem in asRow(row).request?.responses || []" :key="responseItem.question">
              <div class="small text-muted font-weight-bold">{{ responseItem.question }}</div>
              <div class="small" style="white-space: pre-wrap;" v-html="responseItem.answer_html" />
            </div>
          </div>
        </details>
      </td>
      <td class="text-muted text-nowrap" style="width: 1%;">{{ asRow(row).expires_display }}</td>
    </template>
  </TableBase>
</template>
