<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import TableBase from "../shared/components/TableBase.vue";
import {
  buildGroupsRouteUrl,
  readGroupsRouteState,
  type GroupListItem,
  type GroupsBootstrap,
  type GroupsResponse,
  type GroupsRouteState,
} from "./types";
import { fillUrlTemplate } from "../shared/urlTemplates";

const props = defineProps<{
  bootstrap: GroupsBootstrap;
}>();

const rows = ref<GroupListItem[]>([]);
const totalRows = ref(0);
const currentPage = ref(1);
const totalPages = ref(1);
const q = ref("");
const isLoading = ref(false);
const error = ref("");

const columns = computed(() => [
  { key: "group", label: "Group" },
  { key: "description", label: "Description" },
  { key: "members", label: "Members", width: "110px", align: "right" as const },
]);

function currentRouteState(): GroupsRouteState {
  return {
    pathname: window.location.pathname,
    q: q.value,
    page: currentPage.value,
  };
}

function applyRouteState(routeState: GroupsRouteState): void {
  q.value = routeState.q;
  currentPage.value = routeState.page;
}

function syncUrl(pushState: boolean): void {
  const nextUrl = buildGroupsRouteUrl(currentRouteState());
  if (pushState) {
    window.history.pushState(null, "", nextUrl);
    return;
  }
  window.history.replaceState(null, "", nextUrl);
}

function asRow(row: unknown): GroupListItem {
  return row as GroupListItem;
}

function getRowId(row: unknown): string {
  return asRow(row).cn;
}

function buildPageHref(pageNumber: number): string {
  const routeState = currentRouteState();
  routeState.page = pageNumber;
  return buildGroupsRouteUrl(routeState);
}

function groupDetailHref(groupName: string): string {
  return fillUrlTemplate(props.bootstrap.detailUrlTemplate, "__group_name__", groupName);
}

async function load(pushState: boolean): Promise<void> {
  isLoading.value = true;
  error.value = "";

  try {
    const routeUrl = buildGroupsRouteUrl(currentRouteState());
    const query = routeUrl.includes("?") ? routeUrl.slice(routeUrl.indexOf("?")) : "";
    const response = await fetch(`${props.bootstrap.apiUrl}${query}`, {
      headers: {
        Accept: "application/json",
      },
      credentials: "same-origin",
    });

    if (!response.ok) {
      error.value = "Unable to load groups right now.";
      return;
    }

    const payload = (await response.json()) as GroupsResponse;
    rows.value = payload.items;
    totalRows.value = payload.pagination.count;
    currentPage.value = payload.pagination.page;
    totalPages.value = Math.max(payload.pagination.num_pages, 1);
    q.value = payload.q;
    syncUrl(pushState);
  } catch {
    error.value = "Unable to load groups right now.";
  } finally {
    isLoading.value = false;
  }
}

async function onPageChange(pageNumber: number): Promise<void> {
  currentPage.value = pageNumber;
  await load(true);
}

async function submitSearch(): Promise<void> {
  currentPage.value = 1;
  await load(true);
}

async function clearSearch(): Promise<void> {
  q.value = "";
  await submitSearch();
}

onMounted(async () => {
  applyRouteState(readGroupsRouteState(window.location.href));
  window.addEventListener("popstate", () => {
    applyRouteState(readGroupsRouteState(window.location.href));
    void load(false);
  });
  await load(false);
});
</script>

<template>
  <div data-groups-vue-root>
    <TableBase
      :rows="rows"
      :count="totalRows"
      :current-page="currentPage"
      :total-pages="totalPages"
      :is-loading="isLoading"
      :error="error"
      loading-message="Loading groups..."
      checkbox-class="groups-checkbox"
      :columns="columns"
      :page-size="30"
      :get-row-id="getRowId"
      pagination-aria-label="Groups pagination"
      :build-page-href="buildPageHref"
      empty-message="No groups found."
      :show-selection="false"
      @page-change="onPageChange"
    >
      <template #header-meta>
        <form method="get" class="input-group input-group-sm" style="width: 220px;" @submit.prevent="submitSearch">
          <input
            v-model="q"
            type="text"
            name="q"
            class="form-control float-right"
            placeholder="Search groups..."
            aria-label="Search groups"
          >
          <div class="input-group-append">
            <button
              v-if="q"
              type="button"
              class="btn btn-default"
              aria-label="Clear search"
              title="Clear search filter"
              @click="clearSearch"
            >
              <i class="fas fa-times" />
            </button>
            <button type="submit" class="btn btn-default" aria-label="Search" title="Search groups">
              <i class="fas fa-search" />
            </button>
          </div>
        </form>
      </template>

      <template #row-cells="{ row }">
        <td>
          <a :href="groupDetailHref(asRow(row).cn)">{{ asRow(row).cn }}</a>
        </td>
        <td class="text-muted">{{ asRow(row).description }}</td>
        <td class="text-right">
          <span class="badge badge-secondary">{{ asRow(row).member_count }}</span>
        </td>
      </template>
    </TableBase>
  </div>
</template>
