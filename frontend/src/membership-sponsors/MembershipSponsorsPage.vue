<script setup lang="ts">
import { computed, onMounted } from "vue";

import SponsorsTable from "./components/SponsorsTable.vue";
import { useMembershipSponsorsTable } from "./composables/useMembershipSponsorsTable";
import type { MembershipSponsorsBootstrap } from "./types";
import { buildMembershipSponsorsRouteUrl, readMembershipSponsorsRouteState } from "./types";

const props = defineProps<{
  bootstrap: MembershipSponsorsBootstrap;
}>();

const table = useMembershipSponsorsTable(props.bootstrap);

function syncUrl(): void {
  const routeState = readMembershipSponsorsRouteState(window.location.href);
  const nextUrl = buildMembershipSponsorsRouteUrl({
    pathname: routeState.pathname,
    q: table.q.value,
    page: table.currentPage.value,
  });
  window.history.replaceState(null, "", nextUrl);
}

onMounted(async () => {
  const routeState = readMembershipSponsorsRouteState(window.location.href);
  table.currentPage.value = routeState.page;
  if (routeState.q) {
    table.q.value = routeState.q;
  }
  await table.load();
  syncUrl();
});

async function onPageChange(page: number): Promise<void> {
  await table.reloadForPage(page);
  syncUrl();
}

async function onSearch(nextQ: string): Promise<void> {
  await table.reloadForSearch(nextQ);
  syncUrl();
}

function buildPageHref(pageNumber: number): string {
  const routeState = readMembershipSponsorsRouteState(window.location.href);
  return buildMembershipSponsorsRouteUrl({
    pathname: routeState.pathname,
    q: table.q.value,
    page: pageNumber,
  });
}

const totalPages = computed(() => Math.max(1, Math.ceil(table.totalRows.value / props.bootstrap.pageSize)));
</script>

<template>
  <div data-membership-sponsors-vue-root>
    <SponsorsTable
      :rows="table.rows.value"
      :count="table.totalRows.value"
      :current-page="table.currentPage.value"
      :total-pages="totalPages"
      :page-size="bootstrap.pageSize"
      :q="table.q.value"
      :is-loading="table.isLoading.value"
      :error="table.error.value"
      :build-page-href="buildPageHref"
      @page-change="onPageChange"
      @search="onSearch"
    />
  </div>
</template>
