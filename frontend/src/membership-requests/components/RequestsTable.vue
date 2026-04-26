<script setup lang="ts">
import { computed, ref } from "vue";

import TableBase from "../../shared/components/TableBase.vue";
import type { MembershipRequestActionIntent, MembershipRequestRow, MembershipRequestsBootstrap } from "../types";
import {
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

interface BulkActionOption {
  value: string;
  label: string;
}

interface BulkSubmitPayload {
  action: string;
  selectedIds: string[];
  scope?: string;
}

interface SharedColumnDef {
  key: string;
  label: string;
  width?: string;
  noWrap?: boolean;
  align?: "left" | "center" | "right";
}

const props = defineProps<{
  bootstrap: MembershipRequestsBootstrap;
  rows: MembershipRequestRow[];
  count: number;
  currentPage: number;
  totalPages: number;
  isLoading: boolean;
  error: string;
  pageSize: number;
  checkboxClass: string;
  paginationAriaLabel: string;
  buildPageHref: (pageNumber: number) => string;
  columns: ColumnDef[];
  loadingMessage?: string;
  bulkActions?: BulkActionOption[];
  bulkScope?: string;
  bulkFormId?: string;
  bulkSubmitTitle?: string;
  bulkActionPlaceholder?: string;
  bulkSubmitUrl?: string;
}>();

const emit = defineEmits<{
  (event: "page-change", value: number): void;
  (event: "bulk-success", payload: { scope: "pending" | "on_hold" }): void;
  (event: "open-action", payload: MembershipRequestActionIntent): void;
}>();

const isBulkSubmitting = ref(false);
const bulkError = ref("");
const actionError = ref("");

const bulkScope = computed<"pending" | "on_hold">(() => (props.bulkScope === "on_hold" ? "on_hold" : "pending"));

const bulkActionPlaceholder = computed(() => props.bulkActionPlaceholder || "Bulk action…");

const bulkSubmitTitle = computed(() => props.bulkSubmitTitle || "Apply selected action to checked requests");

const bulkSubmitUrl = computed(() => props.bulkSubmitUrl || props.bootstrap.bulkUrl || "/api/v1/membership/requests/bulk");

const nextUrl = computed(() => {
  if (props.bootstrap.nextUrl) {
    return props.bootstrap.nextUrl;
  }
  if (typeof window === "undefined") {
    return "/membership/requests/";
  }
  return `${window.location.pathname}${window.location.search}`;
});

const displayColumns = computed<SharedColumnDef[]>(() => {
  const baseColumns: SharedColumnDef[] = [
    { key: "request", label: "Request", width: "1%", noWrap: true },
    { key: "requested-for", label: "Requested for", width: "30%", noWrap: true },
  ];
  const trailingColumns: SharedColumnDef[] = [{ key: "actions", label: "Actions", width: "15%", align: "right" }];
  return [...baseColumns, ...props.columns, ...trailingColumns];
});

function rowId(row: unknown): string | number {
  return (row as MembershipRequestRow).request_id;
}

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

function asRow(row: unknown): MembershipRequestRow {
  return row as MembershipRequestRow;
}

function onPageChange(pageNumber: number): void {
  emit("page-change", pageNumber);
}

async function submitBulkAction(payload: BulkSubmitPayload): Promise<void> {
  if (!payload.action || payload.selectedIds.length === 0 || isBulkSubmitting.value) {
    return;
  }

  bulkError.value = "";
  isBulkSubmitting.value = true;

  try {
    const response = await fetch(bulkSubmitUrl.value, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-CSRFToken": props.bootstrap.csrfToken || "",
        "Content-Type": "application/json",
      },
      credentials: "same-origin",
      body: JSON.stringify({
        bulk_action: payload.action,
        next: nextUrl.value,
        bulk_scope: bulkScope.value,
        selected: payload.selectedIds.map((id) => Number.parseInt(id, 10)).filter((id) => !Number.isNaN(id)),
      }),
    });

    const result = (await response.json()) as { ok?: boolean; error?: string };
    if (!response.ok || result.ok === false) {
      bulkError.value = result.error || "Failed to apply bulk action.";
      return;
    }

    emit("bulk-success", { scope: bulkScope.value });
  } catch {
    bulkError.value = "Failed to apply bulk action.";
  } finally {
    isBulkSubmitting.value = false;
  }
}

defineSlots<{
  header(): any;
  "header-tools"(): any;
  "header-meta"(): any;
  "row-extra-columns"(props: { row: MembershipRequestRow }): any;
  "empty-state"(): any;
}>();
</script>

<template>
  <TableBase
    :rows="rows"
    :count="count"
    :current-page="currentPage"
    :total-pages="totalPages"
    :is-loading="isLoading"
    :error="error"
    :loading-message="loadingMessage || 'Loading requests...'"
    :checkbox-class="checkboxClass"
    select-all-aria-label="Select all requests"
    :columns="displayColumns"
    :page-size="pageSize"
    :get-row-id="rowId"
    :pagination-aria-label="paginationAriaLabel"
    :build-page-href="buildPageHref"
    :bulk-actions="bulkActions"
    :bulk-action-placeholder="bulkActionPlaceholder"
    :bulk-submit-title="bulkSubmitTitle"
    :bulk-form-id="bulkFormId"
    :bulk-scope="bulkScope"
    :bulk-error="bulkError"
    :bulk-submitting="isBulkSubmitting"
    :header-error="actionError"
    @page-change="onPageChange"
    @bulk-submit="submitBulkAction"
  >
    <template #header-tools>
      <slot name="header-tools" />
    </template>

    <template #header-meta>
      <slot name="header-meta" />
    </template>

    <template #row-cells="{ row }">
      <td class="align-top text-muted text-nowrap" style="width: 1%;">
        <a :href="requestDetailUrl(asRow(row))">Request #{{ asRow(row).request_id }}</a>
        <br>{{ formatLegacyDateTime(asRow(row).requested_at) }}
      </td>
      <td class="align-top">
        <div>
          <template v-if="hasTargetLink(asRow(row))">
            <a :href="targetHref(asRow(row))">{{ asRow(row).target.label }}<span v-if="asRow(row).target.secondary_label" :class="asRow(row).target.kind === 'user' ? 'text-muted small' : 'text-muted'"> ({{ asRow(row).target.secondary_label }})</span></a>
          </template>
          <template v-else>
            <span>{{ asRow(row).target.label }}</span>
            <span v-if="asRow(row).target.deleted" class="text-muted"> (deleted)</span>
          </template>
        </div>
        <div v-if="asRow(row).requested_by.show" class="text-muted small">
          Requested by:
          <a :href="replaceTemplateToken(bootstrap.userProfileTemplate, '__username__', asRow(row).requested_by.username)">{{ membershipRequestActorLabel(asRow(row).requested_by) }}</a>
          <span v-if="asRow(row).requested_by.deleted" class="text-muted"> (deleted)</span>
        </div>
        <div class="mt-2">
          <MembershipNotesCard
            :request-id="asRow(row).request_id"
            :summary-url="noteSummaryUrl(asRow(row))"
            :detail-url="noteDetailUrl(asRow(row))"
            :add-url="noteAddUrl(asRow(row))"
            :request-detail-template="bootstrap.requestDetailTemplate"
            :csrf-token="bootstrap.csrfToken || ''"
            :next-url="nextUrl"
            :can-view="bootstrap.notesCanView"
            :can-write="bootstrap.notesCanWrite"
            :can-vote="bootstrap.notesCanVote"
          />
        </div>
      </td>
      <slot name="row-extra-columns" :row="asRow(row)" />
      <td class="align-top text-right" style="width: 15%;">
        <MembershipRequestRowActions :row="asRow(row)" :bootstrap="bootstrap" @open-action="emit('open-action', $event)" />
      </td>
    </template>

    <template #empty-state>
      <slot name="empty-state">No requests.</slot>
    </template>
  </TableBase>
</template>
