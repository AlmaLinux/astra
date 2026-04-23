/**
 * Composable for managing account invitations table state and data fetching.
 * Mirrors useMembershipRequestsTable for consistency.
 */

import { computed, ref, readonly } from "vue";
import type {
  AccountInvitationRow,
  AccountInvitationsBootstrap,
  DataTablesResponse
} from "../types";

export interface UseAccountInvitationsTableOptions {
  bootstrap: AccountInvitationsBootstrap;
  apiUrl: string;
  scope: "pending" | "accepted";
}

export function useAccountInvitationsTable(options: UseAccountInvitationsTableOptions) {
  const { bootstrap, apiUrl, scope } = options;

  const rows = ref<AccountInvitationRow[]>([]);
  const totalRows = ref(0);
  const currentPage = ref(1);
  const isLoading = ref(false);
  const error = ref<string | null>(null);

  const pageSize = bootstrap.pageSize || 50;
  const totalPages = computed(() => Math.ceil(totalRows.value / pageSize));

  /**
   * Build DataTables-compatible query parameters.
   */
  function buildQuery(pageNum: number, searchTerm: string = ""): URLSearchParams {
    const params = new URLSearchParams();
    const startIndex = (pageNum - 1) * pageSize;

    params.set("draw", String(pageNum));
    params.set("start", String(startIndex));
    params.set("length", String(pageSize));
    params.set("search[value]", searchTerm);
    params.set("search[regex]", "false");
    params.set("order[0][column]", "0");
    params.set("order[0][dir]", "desc");
    params.set("order[0][name]", scope === "pending" ? "invited_at" : "accepted_at");
    params.set("columns[0][data]", "invitation_id");
    params.set("columns[0][name]", scope === "pending" ? "invited_at" : "accepted_at");
    params.set("columns[0][searchable]", "true");
    params.set("columns[0][orderable]", "true");
    params.set("columns[0][search][value]", "");
    params.set("columns[0][search][regex]", "false");

    return params;
  }

  /**
   * Fetch invitations for a specific page.
   */
  async function load(pageNum: number = 1, searchTerm: string = ""): Promise<void> {
    isLoading.value = true;
    error.value = null;

    try {
      const query = buildQuery(pageNum, searchTerm);
      const url = `${apiUrl}?${query.toString()}`;

      const response = await fetch(url, {
        method: "GET",
        headers: {
          "Accept": "application/json",
        },
        credentials: "same-origin",
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const payload = (await response.json()) as DataTablesResponse;
      rows.value = payload.data;
      totalRows.value = payload.recordsFiltered;
      currentPage.value = pageNum;
    } catch (err) {
      error.value = err instanceof Error ? err.message : "Failed to load invitations";
      rows.value = [];
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * Reload data for the current page.
   */
  async function reloadForPage(searchTerm: string = ""): Promise<void> {
    await load(currentPage.value, searchTerm);
  }

  /**
   * Reload data for a specific page.
   */
  async function reloadForPageNum(pageNum: number): Promise<void> {
    await load(pageNum);
  }

  return {
    // State
    rows: readonly(rows),
    totalRows: readonly(totalRows),
    currentPage: readonly(currentPage),
    totalPages,
    isLoading: readonly(isLoading),
    error: readonly(error),
    pageSize,
    scope,

    // Methods
    load,
    reloadForPage,
    reloadForPageNum,
  };
}
