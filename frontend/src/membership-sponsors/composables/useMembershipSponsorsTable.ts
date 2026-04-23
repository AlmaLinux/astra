import { ref } from "vue";

import type { MembershipSponsorsBootstrap, SponsorRow, SponsorsDataTablesResponse } from "../types";

export function useMembershipSponsorsTable(bootstrap: MembershipSponsorsBootstrap) {
  const rows = ref<SponsorRow[]>([]);
  const totalRows = ref(0);
  const currentPage = ref(1);
  const isLoading = ref(false);
  const error = ref("");
  const q = ref(bootstrap.initialQ);

  async function load(): Promise<void> {
    isLoading.value = true;
    error.value = "";

    try {
      const params = new URLSearchParams({
        draw: "1",
        start: String((currentPage.value - 1) * bootstrap.pageSize),
        length: String(bootstrap.pageSize),
        "search[value]": "",
        "search[regex]": "false",
        "order[0][column]": "0",
        "order[0][dir]": "asc",
        "order[0][name]": "expires_at",
        "columns[0][data]": "membership_id",
        "columns[0][name]": "expires_at",
        "columns[0][searchable]": "true",
        "columns[0][orderable]": "true",
        "columns[0][search][value]": "",
        "columns[0][search][regex]": "false",
      });
      if (q.value) {
        params.set("q", q.value);
      }

      const response = await fetch(`${bootstrap.apiUrl}?${params.toString()}`, {
        headers: {
          Accept: "application/json",
        },
        credentials: "same-origin",
      });
      const payload = (await response.json()) as SponsorsDataTablesResponse;
      if (!response.ok) {
        error.value = "Failed to load sponsors.";
        return;
      }
      rows.value = payload.data;
      totalRows.value = payload.recordsFiltered;
    } catch {
      error.value = "Failed to load sponsors.";
    } finally {
      isLoading.value = false;
    }
  }

  async function reloadForPage(page: number): Promise<void> {
    currentPage.value = Math.max(1, page);
    await load();
  }

  async function reloadForSearch(nextQ: string): Promise<void> {
    q.value = nextQ;
    currentPage.value = 1;
    await load();
  }

  return {
    rows,
    totalRows,
    currentPage,
    isLoading,
    error,
    q,
    load,
    reloadForPage,
    reloadForSearch,
  };
}
