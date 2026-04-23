<script setup lang="ts">
import { computed, ref } from "vue";

import TableBase from "../../shared/components/TableBase.vue";
import type { SponsorRow } from "../types";

const props = defineProps<{
  rows: SponsorRow[];
  count: number;
  currentPage: number;
  totalPages: number;
  pageSize: number;
  q: string;
  isLoading: boolean;
  error: string;
  buildPageHref: (pageNumber: number) => string;
}>();

const emit = defineEmits<{
  (event: "page-change", value: number): void;
  (event: "search", value: string): void;
}>();

const searchText = ref(props.q);
const hasSearchValue = computed(() => searchText.value.length > 0);

interface ColumnState {
  key: "organization" | "representative" | "sponsorship_level" | "expires";
  label: string;
  visible: boolean;
}

const columnState = ref<ColumnState[]>([
  { key: "organization", label: "Organization", visible: true },
  { key: "representative", label: "Representative", visible: true },
  { key: "sponsorship_level", label: "Sponsorship Level", visible: true },
  { key: "expires", label: "Expires", visible: true },
]);

const columns = computed(() => {
  return columnState.value
    .filter((column) => column.visible)
    .map((column) => {
      if (column.key === "expires") {
        return { key: column.key, label: column.label, width: "1%", noWrap: true };
      }
      return { key: column.key, label: column.label };
    });
});

function rowId(row: unknown): string | number {
  return (row as SponsorRow).membership_id;
}

function asRow(row: unknown): SponsorRow {
  return row as SponsorRow;
}

function submitSearch(): void {
  emit("search", searchText.value.trim());
}

function clearSearch(): void {
  searchText.value = "";
  submitSearch();
}

function toggleColumn(key: ColumnState["key"]): void {
  for (const column of columnState.value) {
    if (column.key === key) {
      column.visible = !column.visible;
      break;
    }
  }
}

function buildCsv(): string {
  const visibleColumns = columnState.value.filter((column) => column.visible);
  const header = visibleColumns.map((column) => `"${column.label}"`).join(",");
  const lines = props.rows.map((row) => {
    const values = visibleColumns.map((column) => {
      if (column.key === "organization") {
        return row.organization.name;
      }
      if (column.key === "representative") {
        return row.representative.display_label || "-";
      }
      if (column.key === "sponsorship_level") {
        return row.sponsorship_level;
      }
      return row.expires_display;
    });
    return values.map((value) => `"${String(value).replaceAll('"', '""')}"`).join(",");
  });
  return [header, ...lines].join("\n");
}

function download(filename: string, content: string, mimeType: string): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

async function copyRows(): Promise<void> {
  const csv = buildCsv();
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(csv);
  }
}

function exportCsv(): void {
  download("sponsors.csv", buildCsv(), "text/csv;charset=utf-8");
}

function exportExcel(): void {
  download("sponsors.xlsx", buildCsv(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
}

function exportPdf(): void {
  const text = buildCsv();
  download("sponsors.pdf", text, "application/pdf");
}

function printTable(): void {
  window.print();
}

defineSlots<{
  "header-tools"(): any;
  "header-meta"(): any;
  "row-cells"(props: { row: unknown }): any;
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
    loading-message="Loading sponsors..."
    checkbox-class="sponsors-checkbox"
    :columns="columns"
    :page-size="pageSize"
    :get-row-id="rowId"
    pagination-aria-label="Sponsors pagination"
    :build-page-href="buildPageHref"
    empty-message="No active sponsors found."
    :show-selection="false"
    @page-change="emit('page-change', $event)"
  >
    <template #header-tools>
      <div class="btn-group btn-group-sm" role="group" aria-label="Sponsors export controls">
        <button data-export-copy type="button" class="btn btn-default" title="Copy" @click="copyRows">Copy</button>
        <button data-export-csv type="button" class="btn btn-default" title="CSV" @click="exportCsv">CSV</button>
        <button data-export-excel type="button" class="btn btn-default" title="Excel" @click="exportExcel">Excel</button>
        <button data-export-pdf type="button" class="btn btn-default" title="PDF" @click="exportPdf">PDF</button>
        <button data-export-print type="button" class="btn btn-default" title="Print" @click="printTable">Print</button>
      </div>
      <div class="dropdown d-inline-block ml-2">
        <button
          data-colvis-toggle
          class="btn btn-default btn-sm dropdown-toggle"
          type="button"
          data-toggle="dropdown"
          aria-haspopup="true"
          aria-expanded="false"
          title="Column visibility"
        >Columns</button>
        <div class="dropdown-menu p-2" style="min-width: 220px;">
          <div v-for="column in columnState" :key="column.key" class="form-check">
            <input
              :id="`sponsors-col-${column.key}`"
              class="form-check-input"
              type="checkbox"
              :checked="column.visible"
              @change="toggleColumn(column.key)"
            >
            <label class="form-check-label" :for="`sponsors-col-${column.key}`">{{ column.label }}</label>
          </div>
        </div>
      </div>
    </template>

    <template #header-meta>
      <form data-sponsors-search-form class="input-group input-group-sm" style="width: 260px;" @submit.prevent="submitSearch">
        <input
          v-model="searchText"
          type="text"
          name="q"
          class="form-control"
          placeholder="Search"
          aria-label="Search sponsors"
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
          <button type="submit" class="btn btn-default" aria-label="Search" title="Search sponsors">
            <i class="fas fa-search" />
          </button>
        </div>
      </form>
    </template>

    <template #row-cells="{ row }">
      <td v-if="columnState.find((item) => item.key === 'organization')?.visible">
        <a :href="asRow(row).organization.url">{{ asRow(row).organization.name }}</a>
      </td>
      <td v-if="columnState.find((item) => item.key === 'representative')?.visible">
        <template v-if="asRow(row).representative.username && asRow(row).representative.url">
          <a :href="asRow(row).representative.url">{{ asRow(row).representative.display_label }}</a>
        </template>
        <template v-else>-</template>
      </td>
      <td v-if="columnState.find((item) => item.key === 'sponsorship_level')?.visible">
        {{ asRow(row).sponsorship_level }}
      </td>
      <td
        v-if="columnState.find((item) => item.key === 'expires')?.visible"
        class="text-nowrap"
        style="width: 1%;"
      >
        <span v-if="asRow(row).is_expiring_soon" class="text-danger">{{ asRow(row).expires_display }}</span>
        <template v-else>{{ asRow(row).expires_display }}</template>
      </td>
    </template>
  </TableBase>
</template>
