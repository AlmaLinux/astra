<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { MembershipRequestRow, MembershipRequestsBootstrap } from "../types";
import {
  buildPaginationWindow,
  canLinkMembershipRequestTarget,
  formatLegacyDateTime,
  membershipRequestActorLabel,
  replaceTemplateToken,
} from "../types";
import MembershipNotesCard from "./MembershipNotesCard.vue";
import MembershipRequestRowActions from "./MembershipRequestRowActions.vue";

interface ColumnDef {
  key: string;
  label: string;
  width?: string;
  noWrap?: boolean;
}

const props = defineProps<{
  bootstrap: MembershipRequestsBootstrap;
  rows: MembershipRequestRow[];
  count: number;
  currentPage: number;
  totalPages: number;
  isLoading: boolean;
  error: string;
  title?: string;
  checkboxClass: string;
  paginationAriaLabel: string;
  buildPageHref: (pageNumber: number) => string;
  columns: ColumnDef[];
  colspan: number;
  loadingMessage?: string;
}>();

const emit = defineEmits<{
  (event: "page-change", value: number): void;
}>();

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

watch(
  () => props.rows,
  (rows) => {
    selectedIds.value = selectedIds.value.filter((requestId) => rows.some((row) => row.request_id === requestId));
  },
  { deep: true },
);

const allSelected = computed({
  get: () => props.rows.length > 0 && props.rows.every((row) => selectedIds.value.includes(row.request_id)),
  set: (value: boolean) => {
    selectedIds.value = value ? props.rows.map((row) => row.request_id) : [];
  },
});

const paginationWindow = computed(() => buildPaginationWindow(props.totalPages, props.currentPage));

function noteSummaryUrl(row: MembershipRequestRow): string {
  return replaceTemplateToken(props.bootstrap.noteSummaryTemplate, props.bootstrap.requestIdSentinel, row.request_id);
}

function noteDetailUrl(row: MembershipRequestRow): string {
  return replaceTemplateToken(props.bootstrap.noteDetailTemplate, props.bootstrap.requestIdSentinel, row.request_id);
}

function noteAddUrl(row: MembershipRequestRow): string {
  return replaceTemplateToken(props.bootstrap.noteAddTemplate, props.bootstrap.requestIdSentinel, row.request_id);
}

function requestDetailUrl(row: MembershipRequestRow): string {
  return replaceTemplateToken(props.bootstrap.requestDetailTemplate, props.bootstrap.requestIdSentinel, row.request_id);
}

function targetHref(row: MembershipRequestRow): string {
  if (row.target.kind === "user" && row.target.username) {
    return replaceTemplateToken(props.bootstrap.userProfileTemplate, "__username__", row.target.username);
  }
  return replaceTemplateToken(
    props.bootstrap.organizationDetailTemplate,
    props.bootstrap.requestIdSentinel,
    row.target.organization_id ?? "",
  );
}

function hasTargetLink(row: MembershipRequestRow): boolean {
  return canLinkMembershipRequestTarget(row.target);
}

function onPageLinkClick(event: Event, pageNumber: number, disabled: boolean): void {
  event.preventDefault();
  if (disabled || pageNumber === props.currentPage) {
    return;
  }
  selectedIds.value = [];
  emit("page-change", pageNumber);
}

defineSlots<{
  header(): any;
  "row-extra-columns"(props: { row: MembershipRequestRow }): any;
  "empty-state"(): any;
}>();
</script>

<template>
  <div>
    <div v-if="title" class="mt-4 mb-2">
      <h3>{{ title }}</h3>
    </div>
    <div class="card">
      <div class="card-header">
        <slot name="header">
          <div class="d-flex align-items-center justify-content-between flex-wrap" style="gap: 0.5rem;">
            <div class="text-muted">{{ count }}</div>
          </div>
        </slot>
      </div>

      <div class="card-body p-0">
        <div class="table-responsive">
          <table class="table table-striped mb-0 w-100">
            <thead>
              <tr>
                <th style="width: 40px;" class="text-center">
                  <input v-model="allSelected" type="checkbox" :class="checkboxClass" aria-label="Select all requests">
                </th>
                <th class="text-nowrap" style="width: 1%;">Request</th>
                <th class="text-nowrap" style="width: 30%;">Requested for</th>
                <th v-for="col in columns" :key="col.key" :style="col.width ? `width: ${col.width}` : undefined" :class="col.noWrap ? 'text-nowrap' : ''">
                  {{ col.label }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="error">
                <td :colspan="colspan" class="p-3 text-muted">{{ error }}</td>
              </tr>
              <tr v-else-if="isLoading">
                <td :colspan="colspan" class="p-3 text-muted">{{ loadingMessage || 'Loading requests...' }}</td>
              </tr>
              <tr v-else-if="rows.length === 0">
                <td :colspan="colspan" class="p-3 text-muted">
                  <slot name="empty-state">No requests.</slot>
                </td>
              </tr>
              <template v-for="row in rows" :key="row.request_id">
                <tr>
                  <td class="text-center align-top">
                    <input v-model="selectedIds" :class="checkboxClass" type="checkbox" name="selected" :value="row.request_id" :aria-label="`Select request ${row.request_id}`">
                  </td>
                  <td class="align-top text-muted text-nowrap" style="width: 1%;">
                    <a :href="requestDetailUrl(row)">Request #{{ row.request_id }}</a>
                    <br>{{ formatLegacyDateTime(row.requested_at) }}
                  </td>
                  <td class="align-top">
                    <div>
                      <template v-if="hasTargetLink(row)">
                        <a :href="targetHref(row)">{{ row.target.label }}<span v-if="row.target.secondary_label" :class="row.target.kind === 'user' ? 'text-muted small' : 'text-muted'"> ({{ row.target.secondary_label }})</span></a>
                      </template>
                      <template v-else>
                        <span>{{ row.target.label }}</span>
                        <span v-if="row.target.deleted" class="text-muted"> (deleted)</span>
                      </template>
                    </div>
                    <div v-if="row.requested_by.show" class="text-muted small">
                      Requested by:
                      <a :href="replaceTemplateToken(bootstrap.userProfileTemplate, '__username__', row.requested_by.username)">{{ membershipRequestActorLabel(row.requested_by) }}</a>
                      <span v-if="row.requested_by.deleted" class="text-muted"> (deleted)</span>
                    </div>
                    <div class="mt-2">
                      <MembershipNotesCard
                        :request-id="row.request_id"
                        :summary-url="noteSummaryUrl(row)"
                        :detail-url="noteDetailUrl(row)"
                        :add-url="noteAddUrl(row)"
                        :csrf-token="bootstrap.csrfToken || ''"
                        :next-url="nextUrl"
                        :can-view="bootstrap.notesCanView"
                        :can-write="bootstrap.notesCanWrite"
                        :can-vote="bootstrap.notesCanVote"
                      />
                    </div>
                  </td>
                  <slot name="row-extra-columns" :row="row" />
                  <td class="align-top text-right" style="width: 15%;">
                    <MembershipRequestRowActions :row="row" :bootstrap="bootstrap" />
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
        <div class="p-3 border-top" v-if="totalPages > 1">
          <ul class="pagination pagination-sm m-0 float-right" :aria-label="paginationAriaLabel">
            <li class="page-item" :class="{ disabled: currentPage <= 1 }">
              <a class="page-link" :href="currentPage <= 1 ? '#' : buildPageHref(currentPage - 1)" aria-label="Previous" @click="onPageLinkClick($event, currentPage - 1, currentPage <= 1)">«</a>
            </li>
            <li v-if="paginationWindow.showFirst" class="page-item">
              <a class="page-link" :href="buildPageHref(1)" @click="onPageLinkClick($event, 1, false)">1</a>
            </li>
            <li v-if="paginationWindow.showFirst" class="page-item disabled"><span class="page-link">…</span></li>
            <li
              v-for="pageNumber in paginationWindow.pageNumbers"
              :key="pageNumber"
              class="page-item"
              :class="{ active: pageNumber === currentPage }"
            >
              <a class="page-link" :href="buildPageHref(pageNumber)" @click="onPageLinkClick($event, pageNumber, false)">{{ pageNumber }}</a>
            </li>
            <li v-if="paginationWindow.showLast" class="page-item disabled"><span class="page-link">…</span></li>
            <li v-if="paginationWindow.showLast" class="page-item">
              <a class="page-link" :href="buildPageHref(totalPages)" @click="onPageLinkClick($event, totalPages, false)">{{ totalPages }}</a>
            </li>
            <li class="page-item" :class="{ disabled: currentPage >= totalPages }">
              <a class="page-link" :href="currentPage >= totalPages ? '#' : buildPageHref(currentPage + 1)" aria-label="Next" @click="onPageLinkClick($event, currentPage + 1, currentPage >= totalPages)">»</a>
            </li>
          </ul>
        </div>
      </div>
    </div>
  </div>
</template>
