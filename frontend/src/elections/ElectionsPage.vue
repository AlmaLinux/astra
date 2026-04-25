<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import {
  buildElectionsRouteUrl,
  readElectionsRouteState,
  type ElectionsBootstrap,
  type ElectionsResponse,
  type ElectionsRouteState,
  type ElectionListItem,
} from "./types";

const props = defineProps<{
  bootstrap: ElectionsBootstrap;
}>();

const rows = ref<ElectionListItem[]>([]);
const currentPage = ref(1);
const canManageElections = ref(false);
const isLoading = ref(false);
const error = ref("");

const openRows = computed(() => rows.value.filter((row) => row.status === "open" || row.status === "draft"));
const pastRows = computed(() => rows.value.filter((row) => row.status === "closed" || row.status === "tallied"));

function currentRouteState(): ElectionsRouteState {
  return {
    pathname: window.location.pathname,
    page: currentPage.value,
  };
}

function applyRouteState(routeState: ElectionsRouteState): void {
  currentPage.value = routeState.page;
}

function syncUrl(pushState: boolean): void {
  const nextUrl = buildElectionsRouteUrl(currentRouteState());
  if (pushState) {
    window.history.pushState(null, "", nextUrl);
    return;
  }
  window.history.replaceState(null, "", nextUrl);
}

function electionHref(row: ElectionListItem): string {
  if (row.status === "draft" && canManageElections.value && row.edit_url) {
    return row.edit_url;
  }
  return row.detail_url;
}

function formatWindow(row: ElectionListItem): string {
  return `${formatDate(row.start_datetime)} UTC → ${formatDate(row.end_datetime)} UTC`;
}

function formatDate(value: string): string {
  return value.replace("T", " ").replace(/([+-]\d\d:\d\d|Z)$/, "").slice(0, 16);
}

function statusBadgeClass(status: string): string {
  if (status === "draft") {
    return "badge badge-warning";
  }
  if (status === "open") {
    return "badge badge-success";
  }
  return "badge badge-secondary";
}

async function load(pushState: boolean): Promise<void> {
  isLoading.value = true;
  error.value = "";

  try {
    const routeUrl = buildElectionsRouteUrl(currentRouteState());
    const query = routeUrl.includes("?") ? routeUrl.slice(routeUrl.indexOf("?")) : "";
    const response = await fetch(`${props.bootstrap.apiUrl}${query}`, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });

    if (!response.ok) {
      error.value = "Unable to load elections right now.";
      return;
    }

    const payload = (await response.json()) as ElectionsResponse;
    rows.value = payload.items;
    currentPage.value = payload.pagination.page;
    canManageElections.value = payload.can_manage_elections;
    syncUrl(pushState);
  } catch {
    error.value = "Unable to load elections right now.";
  } finally {
    isLoading.value = false;
  }
}

onMounted(async () => {
  applyRouteState(readElectionsRouteState(window.location.href));
  window.addEventListener("popstate", () => {
    applyRouteState(readElectionsRouteState(window.location.href));
    void load(false);
  });
  await load(false);
});
</script>

<template>
  <div data-elections-vue-root>
    <div class="row">
      <div class="col-lg-12">
        <div class="card card-outline card-primary">
          <div class="card-header">
            <h3 class="card-title">Open elections</h3>
          </div>
          <div class="card-body">
            <div class="list-group">
              <div v-if="error" class="list-group-item text-muted mb-0">{{ error }}</div>
              <div v-else-if="isLoading" class="list-group-item text-muted mb-0">Loading elections...</div>
              <template v-else-if="openRows.length > 0">
                <a
                  v-for="election in openRows"
                  :key="election.id"
                  class="list-group-item list-group-item-action d-flex justify-content-between align-items-center"
                  :href="electionHref(election)"
                >
                  <div>
                    <div class="font-weight-bold">{{ election.name }}</div>
                    <div class="text-muted small">{{ formatWindow(election) }}</div>
                  </div>
                  <span :class="statusBadgeClass(election.status)">{{ election.status }}</span>
                </a>
              </template>
              <div v-else class="list-group-item text-muted mb-0">No open elections.</div>
            </div>

            <div class="text-muted small mb-2">
              Note: Election administrators may extend the end date if quorum is not reached.
            </div>
          </div>
        </div>

        <div class="card card-outline card-secondary mb-0">
          <div class="card-header">
            <h3 class="card-title">Past elections</h3>

            <div class="card-tools">
              <button type="button" class="btn btn-tool" data-card-widget="collapse" title="Show or hide past elections">
                <i class="fas fa-minus"></i>
              </button>
            </div>
          </div>
          <div class="card-body">
            <div class="list-group">
              <div v-if="error" class="list-group-item text-muted mb-0">{{ error }}</div>
              <div v-else-if="isLoading" class="list-group-item text-muted mb-0">Loading elections...</div>
              <template v-else-if="pastRows.length > 0">
                <a
                  v-for="election in pastRows"
                  :key="election.id"
                  class="list-group-item list-group-item-action d-flex justify-content-between align-items-center"
                  :href="election.detail_url"
                >
                  <div>
                    <div class="font-weight-bold">{{ election.name }}</div>
                    <div class="text-muted small">{{ formatWindow(election) }}</div>
                  </div>
                  <span class="badge badge-secondary">{{ election.status }}</span>
                </a>
              </template>
              <div v-else class="list-group-item text-muted mb-0">No past elections.</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>