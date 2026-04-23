<script setup lang="ts">
import { computed, ref, useSlots, watch } from "vue";

interface ColumnDef {
  key: string;
  label: string;
  width?: string;
  noWrap?: boolean;
  align?: "left" | "center" | "right";
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

const props = defineProps<{
  rows: unknown[];
  count: number;
  currentPage: number;
  totalPages: number;
  isLoading: boolean;
  error: string | null;
  loadingMessage?: string;
  checkboxClass: string;
  selectAllAriaLabel?: string;
  columns: ColumnDef[];
  emptyMessage?: string;
  pageSize?: number;
  getRowId: (row: unknown) => string | number;
  paginationAriaLabel: string;
  buildPageHref?: (pageNumber: number) => string;
  bulkActions?: BulkActionOption[];
  bulkActionPlaceholder?: string;
  bulkSubmitTitle?: string;
  bulkFormId?: string;
  bulkScope?: string;
  bulkError?: string;
  bulkValidationMessage?: string;
  bulkSubmitting?: boolean;
  headerError?: string;
  showSelection?: boolean;
}>();

const emit = defineEmits<{
  (event: "page-change", value: number): void;
  (event: "bulk-submit", payload: BulkSubmitPayload): void;
}>();

const selectedIds = ref<string[]>([]);
const selectedAction = ref("");
const localBulkError = ref("");
const slots = useSlots();

const showSelection = computed(() => props.showSelection !== false);
const colspan = computed(() => props.columns.length + (showSelection.value ? 1 : 0));
const hasBulkActions = computed(() => (props.bulkActions?.length || 0) > 0);
const bulkActionPlaceholder = computed(() => props.bulkActionPlaceholder || "Bulk action…");
const bulkSubmitTitle = computed(() => props.bulkSubmitTitle || "Apply selected action to checked rows");
const selectAllAriaLabel = computed(() => props.selectAllAriaLabel || "Select all rows");
const effectiveBulkError = computed(() => props.bulkError || localBulkError.value);
const showFooter = computed(() => props.totalPages > 1 || Boolean(slots["footer-meta"]) || Boolean(props.pageSize && props.count > 0));

function rowIdString(row: unknown): string {
  return String(props.getRowId(row));
}

watch(
  () => props.rows,
  (rows) => {
    const validIds = new Set(rows.map((row) => rowIdString(row)));
    selectedIds.value = selectedIds.value.filter((selectedId) => validIds.has(selectedId));
  },
  { deep: true },
);

watch(selectedAction, () => {
  if (localBulkError.value) {
    localBulkError.value = "";
  }
});

watch(
  selectedIds,
  () => {
    if (localBulkError.value) {
      localBulkError.value = "";
    }
  },
  { deep: true },
);

const allSelected = computed({
  get: () => props.rows.length > 0 && props.rows.every((row) => selectedIds.value.includes(rowIdString(row))),
  set: (value: boolean) => {
    if (value) {
      selectedIds.value = props.rows.map((row) => rowIdString(row));
      return;
    }
    selectedIds.value = [];
  },
});

interface PaginationWindow {
  pageNumbers: number[];
  showFirst: boolean;
  showLast: boolean;
}

const paginationWindow = computed<PaginationWindow>(() => {
  if (props.totalPages <= 10) {
    return {
      pageNumbers: Array.from({ length: props.totalPages }, (_unused, index) => index + 1),
      showFirst: false,
      showLast: false,
    };
  }

  const start = Math.max(1, props.currentPage - 2);
  const end = Math.min(props.totalPages, props.currentPage + 2);
  const pageNumbers: number[] = [];
  for (let pageNumber = start; pageNumber <= end; pageNumber += 1) {
    pageNumbers.push(pageNumber);
  }

  return {
    pageNumbers,
    showFirst: !pageNumbers.includes(1),
    showLast: !pageNumbers.includes(props.totalPages),
  };
});

function pageHref(pageNumber: number): string {
  if (!props.buildPageHref) {
    return "#";
  }
  return props.buildPageHref(pageNumber);
}

function onPageLinkClick(event: Event, pageNumber: number, disabled: boolean): void {
  event.preventDefault();
  if (disabled || pageNumber === props.currentPage) {
    return;
  }
  selectedIds.value = [];
  emit("page-change", pageNumber);
}

function submitBulkAction(): void {
  if (!selectedAction.value || selectedIds.value.length === 0 || props.bulkSubmitting) {
    if (!selectedAction.value || selectedIds.value.length === 0) {
      localBulkError.value = props.bulkValidationMessage || "Please select an action and at least one row.";
    }
    return;
  }

  localBulkError.value = "";
  emit("bulk-submit", {
    action: selectedAction.value,
    scope: props.bulkScope,
    selectedIds: [...selectedIds.value],
  });
}

function alignClass(col: ColumnDef): string {
  if (col.align === "center") {
    return "text-center";
  }
  if (col.align === "right") {
    return "text-right";
  }
  return "";
}

defineSlots<{
  "header-tools"(): any;
  "header-meta"(): any;
  "row-cells"(props: { row: unknown }): any;
  "empty-state"(): any;
  "footer-meta"(props: { selectedCount: number }): any;
}>();
</script>

<template>
  <div class="card">
    <div class="card-header">
        <div class="d-flex align-items-center justify-content-between flex-wrap" style="gap: 0.5rem;">
          <div class="d-flex align-items-center flex-wrap" style="gap: 0.5rem;">
            <form
              v-if="hasBulkActions"
              :id="bulkFormId"
              class="form-inline"
              @submit.prevent="submitBulkAction"
            >
              <input v-if="bulkScope" type="hidden" name="bulk_scope" :value="bulkScope">
              <div class="input-group input-group-sm">
                <select v-model="selectedAction" name="bulk_action" class="custom-select custom-select-sm" aria-label="Bulk action">
                  <option value="">{{ bulkActionPlaceholder }}</option>
                  <option v-for="option in bulkActions" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
                <div class="input-group-append">
                  <button
                    type="submit"
                    class="btn btn-default"
                    :title="bulkSubmitTitle"
                    :disabled="selectedIds.length === 0 || !selectedAction || bulkSubmitting"
                  >Apply</button>
                </div>
              </div>
              <input v-for="rowId in selectedIds" :key="rowId" type="hidden" name="selected" :value="rowId">
            </form>
            <div v-if="effectiveBulkError" class="small text-danger">{{ effectiveBulkError }}</div>
            <div v-if="headerError" class="small text-danger">{{ headerError }}</div>
            <slot name="header-tools" />
          </div>
          <slot name="header-meta">
            <div class="text-muted">{{ count }}</div>
          </slot>
        </div>
      </div>

      <div class="card-body p-0">
        <div class="table-responsive">
          <table class="table table-striped mb-0 w-100">
            <thead>
              <tr>
                <th v-if="showSelection" style="width: 40px;" class="text-center">
                  <input v-model="allSelected" type="checkbox" :class="checkboxClass" :aria-label="selectAllAriaLabel">
                </th>
                <th
                  v-for="col in columns"
                  :key="col.key"
                  :style="col.width ? `width: ${col.width}` : undefined"
                  :class="[col.noWrap ? 'text-nowrap' : '', alignClass(col)]"
                >
                  {{ col.label }}
                </th>
              </tr>
            </thead>

            <tbody>
              <tr v-if="error">
                <td :colspan="colspan" class="p-3 text-muted">{{ error }}</td>
              </tr>
              <tr v-else-if="isLoading">
                <td :colspan="colspan" class="p-3 text-muted">{{ loadingMessage || 'Loading...' }}</td>
              </tr>
              <tr v-else-if="rows.length === 0">
                <td :colspan="colspan" class="p-3 text-muted">
                  <slot name="empty-state">{{ emptyMessage || 'No items.' }}</slot>
                </td>
              </tr>

              <template v-for="row in rows" :key="rowIdString(row)">
                <tr>
                  <td v-if="showSelection" class="text-center align-top">
                    <input
                      v-model="selectedIds"
                      :class="checkboxClass"
                      type="checkbox"
                      name="selected"
                      :value="rowIdString(row)"
                      :aria-label="`Select row ${rowIdString(row)}`"
                    >
                  </td>
                  <slot name="row-cells" :row="row" />
                </tr>
              </template>
            </tbody>
          </table>
        </div>

        <div v-if="showFooter" class="border-top p-2 clearfix">
          <div class="float-left text-muted small">
            <template v-if="pageSize && count > 0">
              Showing {{ (currentPage - 1) * pageSize + 1 }}–{{ Math.min(currentPage * pageSize, count) }} of {{ count }}
            </template>
            <slot name="footer-meta" :selected-count="selectedIds.length" />
          </div>

          <ul v-if="totalPages > 1" class="pagination pagination-sm m-0 float-right" :aria-label="paginationAriaLabel">
            <li class="page-item" :class="{ disabled: currentPage <= 1 }">
              <a
                class="page-link"
                :href="currentPage <= 1 ? '#' : pageHref(currentPage - 1)"
                aria-label="Previous"
                @click="onPageLinkClick($event, currentPage - 1, currentPage <= 1)"
              >«</a>
            </li>
            <li v-if="paginationWindow.showFirst" class="page-item">
              <a class="page-link" :href="pageHref(1)" @click="onPageLinkClick($event, 1, false)">1</a>
            </li>
            <li v-if="paginationWindow.showFirst" class="page-item disabled"><span class="page-link">…</span></li>
            <li
              v-for="pageNumber in paginationWindow.pageNumbers"
              :key="pageNumber"
              class="page-item"
              :class="{ active: pageNumber === currentPage }"
            >
              <a class="page-link" :href="pageHref(pageNumber)" @click="onPageLinkClick($event, pageNumber, false)">{{ pageNumber }}</a>
            </li>
            <li v-if="paginationWindow.showLast" class="page-item disabled"><span class="page-link">…</span></li>
            <li v-if="paginationWindow.showLast" class="page-item">
              <a class="page-link" :href="pageHref(totalPages)" @click="onPageLinkClick($event, totalPages, false)">{{ totalPages }}</a>
            </li>
            <li class="page-item" :class="{ disabled: currentPage >= totalPages }">
              <a
                class="page-link"
                :href="currentPage >= totalPages ? '#' : pageHref(currentPage + 1)"
                aria-label="Next"
                @click="onPageLinkClick($event, currentPage + 1, currentPage >= totalPages)"
              >»</a>
            </li>
          </ul>
        </div>
      </div>
    </div>
</template>
