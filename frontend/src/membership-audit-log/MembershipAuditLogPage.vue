<script setup lang="ts">
import { computed, onMounted } from "vue";

import AuditLogTable from "./components/AuditLogTable.vue";
import { useMembershipAuditLogTable } from "./composables/useMembershipAuditLogTable";
import type { MembershipAuditLogBootstrap } from "./types";
import { buildMembershipAuditLogRouteUrl, readMembershipAuditLogRouteState } from "./types";

const props = defineProps<{
  bootstrap: MembershipAuditLogBootstrap;
}>();

const table = useMembershipAuditLogTable(props.bootstrap);

function syncUrl(): void {
  const routeState = readMembershipAuditLogRouteState(window.location.href);
  const nextUrl = buildMembershipAuditLogRouteUrl({
    pathname: routeState.pathname,
    q: table.q.value,
    page: table.currentPage.value,
    username: props.bootstrap.initialUsername,
    organization: props.bootstrap.initialOrganization,
  });
  window.history.replaceState(null, "", nextUrl);
}

onMounted(async () => {
  const routeState = readMembershipAuditLogRouteState(window.location.href);
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
  const routeState = readMembershipAuditLogRouteState(window.location.href);
  return buildMembershipAuditLogRouteUrl({
    pathname: routeState.pathname,
    q: table.q.value,
    page: pageNumber,
    username: props.bootstrap.initialUsername,
    organization: props.bootstrap.initialOrganization,
  });
}

const totalPages = computed(() => Math.max(1, Math.ceil(table.totalRows.value / props.bootstrap.pageSize)));
</script>

<template>
  <div data-membership-audit-log-vue-root>
    <AuditLogTable
      :rows="table.rows.value"
      :count="table.totalRows.value"
      :current-page="table.currentPage.value"
      :total-pages="totalPages"
      :page-size="bootstrap.pageSize"
      :q="table.q.value"
      :is-loading="table.isLoading.value"
      :error="table.error.value"
      :user-profile-url-template="bootstrap.userProfileUrlTemplate"
      :organization-detail-url-template="bootstrap.organizationDetailUrlTemplate"
      :membership-request-detail-url-template="bootstrap.membershipRequestDetailUrlTemplate"
      :build-page-href="buildPageHref"
      @page-change="onPageChange"
      @search="onSearch"
    />
  </div>
</template>
