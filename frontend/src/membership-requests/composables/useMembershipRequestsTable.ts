import { ref } from "vue";

import type { MembershipRequestRow, PendingFilterOption } from "../types";

interface UseMembershipRequestsTableOptions {
  url: string;
  pageSize: number;
  orderName: string;
  initialPage: number;
  initialFilter?: string;
}

interface DataTablesPayload {
  data: MembershipRequestRow[];
  recordsFiltered: number;
  pending_filter?: {
    selected: string;
    options: PendingFilterOption[];
  };
}

export function useMembershipRequestsTable(options: UseMembershipRequestsTableOptions) {
  const rows = ref<MembershipRequestRow[]>([]);
  const totalRows = ref(0);
  const currentPage = ref(Math.max(1, options.initialPage));
  const selectedFilter = ref(options.initialFilter ?? "all");
  const filterOptions = ref<PendingFilterOption[]>([]);
  const isLoading = ref(false);
  const error = ref("");

  async function load(): Promise<void> {
    isLoading.value = true;
    error.value = "";

    try {
      const params = new URLSearchParams({
        draw: "1",
        start: String((currentPage.value - 1) * options.pageSize),
        length: String(options.pageSize),
        "search[value]": "",
        "search[regex]": "false",
        "order[0][column]": "0",
        "order[0][dir]": "asc",
        "order[0][name]": options.orderName,
        "columns[0][data]": "request_id",
        "columns[0][name]": options.orderName,
        "columns[0][searchable]": "true",
        "columns[0][orderable]": "true",
        "columns[0][search][value]": "",
        "columns[0][search][regex]": "false",
      });
      if (options.initialFilter !== undefined) {
        params.set("queue_filter", selectedFilter.value);
      }

      const response = await fetch(`${options.url}?${params.toString()}`, {
        headers: {
          Accept: "application/json",
        },
        credentials: "same-origin",
      });
      const payload = (await response.json()) as DataTablesPayload;
      if (!response.ok) {
        error.value = "Failed to load membership requests.";
        return;
      }

      rows.value = payload.data;
      totalRows.value = payload.recordsFiltered;
      if (payload.pending_filter) {
        selectedFilter.value = payload.pending_filter.selected;
        filterOptions.value = payload.pending_filter.options;
      }
    } catch {
      error.value = "Failed to load membership requests.";
    } finally {
      isLoading.value = false;
    }
  }

  async function reloadForPage(page: number): Promise<void> {
    currentPage.value = Math.max(1, page);
    await load();
  }

  async function reloadForFilter(filter: string): Promise<void> {
    selectedFilter.value = filter;
    currentPage.value = 1;
    await load();
  }

  return {
    rows,
    totalRows,
    currentPage,
    selectedFilter,
    filterOptions,
    isLoading,
    error,
    load,
    reloadForPage,
    reloadForFilter,
  };
}