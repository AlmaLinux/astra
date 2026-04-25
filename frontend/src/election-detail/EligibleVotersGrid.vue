<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import WidgetGrid from "../shared/components/WidgetGrid.vue";
import WidgetUser from "../shared/components/WidgetUser.vue";
import ElectionCredentialResendControls from "./ElectionCredentialResendControls.vue";
import type { ElectionsPagination } from "../elections/types";
import type {
  ElectionCredentialResendBootstrap,
  EligibleVoterItem,
  EligibleVotersBootstrap,
  EligibleVotersResponse,
  IneligibleVotersResponse,
  IneligibleVoterDetails,
} from "./types";

const props = defineProps<{
  bootstrap: EligibleVotersBootstrap;
}>();

const eligibleItems = ref<EligibleVoterItem[]>([]);
const eligiblePagination = ref<ElectionsPagination | null>(null);
const eligibleUsernames = ref<string[]>([]);
const ineligibleItems = ref<EligibleVoterItem[]>([]);
const ineligiblePagination = ref<ElectionsPagination | null>(null);
const ineligibleDetailsByUsername = ref<Record<string, IneligibleVoterDetails>>({});
const eligibleError = ref("");
const ineligibleError = ref("");
const isEligibleLoading = ref(false);
const isIneligibleLoading = ref(false);
const eligibleQuery = ref("");
const ineligibleQuery = ref("");
const eligiblePage = ref(1);
const ineligiblePage = ref(1);
const hasLoadedEligible = ref(false);
const hasLoadedIneligible = ref(false);
const selectedIneligibleUsername = ref("");
const isIneligibleModalVisible = ref(false);

const credentialResendBootstrap = computed<ElectionCredentialResendBootstrap | null>(() => {
  if (!props.bootstrap.sendMailCredentialsApiUrl) {
    return null;
  }
  return {
    sendMailCredentialsApiUrl: props.bootstrap.sendMailCredentialsApiUrl,
    eligibleUsernames: eligibleUsernames.value,
  };
});

const selectedIneligibleDetails = computed<IneligibleVoterDetails | null>(() => {
  if (!selectedIneligibleUsername.value) {
    return null;
  }
  return ineligibleDetailsByUsername.value[selectedIneligibleUsername.value] || null;
});

function currentSearchParams(): URLSearchParams {
  return new URLSearchParams(window.location.search);
}

function queryParam(name: string): string {
  return String(currentSearchParams().get(name) || "").trim();
}

function pageParam(name: string): number {
  const parsed = Number.parseInt(currentSearchParams().get(name) || "1", 10);
  return Number.isNaN(parsed) || parsed < 1 ? 1 : parsed;
}

function initializeStateFromLocation(): void {
  eligibleQuery.value = queryParam("eligible_q");
  ineligibleQuery.value = queryParam("ineligible_q");
  eligiblePage.value = pageParam("eligible_page");
  ineligiblePage.value = pageParam("ineligible_page");
}

function buildEligibleApiUrl(nextPage: number): string {
  const url = new URL(props.bootstrap.eligibleVotersApiUrl, window.location.origin);
  const nextQuery = eligibleQuery.value.trim();
  if (nextQuery) {
    url.searchParams.set("q", nextQuery);
  }
  url.searchParams.set("page", String(nextPage));
  return `${url.pathname}${url.search}`;
}

function buildIneligibleApiUrl(nextPage: number): string {
  const url = new URL(props.bootstrap.ineligibleVotersApiUrl, window.location.origin);
  const nextQuery = ineligibleQuery.value.trim();
  if (nextQuery) {
    url.searchParams.set("q", nextQuery);
  }
  url.searchParams.set("page", String(nextPage));
  return `${url.pathname}${url.search}`;
}

function syncLocation(): void {
  const url = new URL(window.location.href);

  if (eligibleQuery.value.trim()) {
    url.searchParams.set("eligible_q", eligibleQuery.value.trim());
  } else {
    url.searchParams.delete("eligible_q");
  }
  if (eligiblePage.value > 1) {
    url.searchParams.set("eligible_page", String(eligiblePage.value));
  } else {
    url.searchParams.delete("eligible_page");
  }

  if (ineligibleQuery.value.trim()) {
    url.searchParams.set("ineligible_q", ineligibleQuery.value.trim());
  } else {
    url.searchParams.delete("ineligible_q");
  }
  if (ineligiblePage.value > 1) {
    url.searchParams.set("ineligible_page", String(ineligiblePage.value));
  } else {
    url.searchParams.delete("ineligible_page");
  }

  window.history.replaceState({}, "", `${url.pathname}${url.search}`);
}

function buildEligiblePageHref(pageNumber: number): string {
  const url = new URL(window.location.href);
  if (eligibleQuery.value.trim()) {
    url.searchParams.set("eligible_q", eligibleQuery.value.trim());
  } else {
    url.searchParams.delete("eligible_q");
  }
  if (pageNumber > 1) {
    url.searchParams.set("eligible_page", String(pageNumber));
  } else {
    url.searchParams.delete("eligible_page");
  }
  return `${url.pathname}${url.search}`;
}

function buildIneligiblePageHref(pageNumber: number): string {
  const url = new URL(window.location.href);
  if (ineligibleQuery.value.trim()) {
    url.searchParams.set("ineligible_q", ineligibleQuery.value.trim());
  } else {
    url.searchParams.delete("ineligible_q");
  }
  if (pageNumber > 1) {
    url.searchParams.set("ineligible_page", String(pageNumber));
  } else {
    url.searchParams.delete("ineligible_page");
  }
  return `${url.pathname}${url.search}`;
}

function eligibleVoterItem(item: unknown): EligibleVoterItem {
  return item as EligibleVoterItem;
}

async function loadEligible(nextPage = eligiblePage.value): Promise<void> {
  eligiblePage.value = nextPage;
  isEligibleLoading.value = true;
  eligibleError.value = "";

  try {
    const response = await fetch(buildEligibleApiUrl(nextPage), {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!response.ok) {
      eligibleError.value = "Unable to load eligible voters right now.";
      return;
    }
    const payload = (await response.json()) as EligibleVotersResponse;
    eligibleItems.value = payload.eligible_voters.items;
    eligiblePagination.value = payload.eligible_voters.pagination;
    eligibleUsernames.value = payload.eligible_voters.usernames;
    hasLoadedEligible.value = true;
    syncLocation();
  } catch {
    eligibleError.value = "Unable to load eligible voters right now.";
  } finally {
    isEligibleLoading.value = false;
  }
}

async function loadIneligible(nextPage = ineligiblePage.value): Promise<void> {
  ineligiblePage.value = nextPage;
  isIneligibleLoading.value = true;
  ineligibleError.value = "";

  try {
    const response = await fetch(buildIneligibleApiUrl(nextPage), {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!response.ok) {
      ineligibleError.value = "Unable to load ineligible voters right now.";
      return;
    }
    const payload = (await response.json()) as IneligibleVotersResponse;
    ineligibleItems.value = payload.ineligible_voters.items;
    ineligiblePagination.value = payload.ineligible_voters.pagination;
    ineligibleDetailsByUsername.value = payload.ineligible_voters.details_by_username;
    hasLoadedIneligible.value = true;
    syncLocation();
  } catch {
    ineligibleError.value = "Unable to load ineligible voters right now.";
  } finally {
    isIneligibleLoading.value = false;
  }
}

async function loadEligiblePage(pageNumber: number): Promise<void> {
  await loadEligible(pageNumber);
}

async function loadIneligiblePage(pageNumber: number): Promise<void> {
  await loadIneligible(pageNumber);
}

async function submitEligibleSearch(): Promise<void> {
  eligibleQuery.value = eligibleQuery.value.trim();
  await loadEligible(1);
}

async function submitIneligibleSearch(): Promise<void> {
  ineligibleQuery.value = ineligibleQuery.value.trim();
  await loadIneligible(1);
}

async function clearEligibleSearch(): Promise<void> {
  eligibleQuery.value = "";
  await loadEligible(1);
}

async function clearIneligibleSearch(): Promise<void> {
  ineligibleQuery.value = "";
  await loadIneligible(1);
}

async function handleEligibleCardToggle(): Promise<void> {
  if (!hasLoadedEligible.value) {
    await loadEligible();
  }
}

async function handleIneligibleCardToggle(): Promise<void> {
  if (!hasLoadedIneligible.value) {
    await loadIneligible();
  }
}

function reasonText(reason: string): string {
  if (reason === "no_membership") return "No qualifying membership or sponsorship found.";
  if (reason === "expired") return "Membership or sponsorship was not active at the reference date.";
  if (reason === "too_new") return "Membership or sponsorship is active, but too new at the reference date.";
  return reason;
}

function detailValue(value: number | string | null): string {
  return value === null ? "" : String(value);
}

function openIneligibleDetails(username: string): void {
  if (!ineligibleDetailsByUsername.value[username]) {
    return;
  }
  selectedIneligibleUsername.value = username;
  isIneligibleModalVisible.value = true;
}

function hideIneligibleDetails(): void {
  isIneligibleModalVisible.value = false;
}

function handleIneligibleUserClick(event: Event, item: EligibleVoterItem): void {
  const target = event.target as HTMLElement | null;
  const link = target?.closest("a[href]");
  if (!link) {
    return;
  }
  event.preventDefault();
  openIneligibleDetails(item.username);
}

onMounted(async () => {
  initializeStateFromLocation();
});
</script>

<template>
  <div data-election-eligible-voters-vue-root>
    <div class="row">
      <div class="col-12">
        <div class="card card-outline card-primary collapsed-card election-voter-card election-collapsible-card">
          <div class="card-header">
            <h3 class="card-title">Eligible voters</h3>

            <div class="card-tools">
              <form class="js-card-search input-group input-group-sm" style="width: 220px;" method="get" @submit.prevent="submitEligibleSearch">
                <input
                  v-model="eligibleQuery"
                  type="text"
                  name="eligible_q"
                  class="form-control float-right"
                  placeholder="Search users..."
                  aria-label="Search users"
                >
                <div class="input-group-append">
                  <button
                    v-if="eligibleQuery"
                    type="button"
                    class="btn btn-default"
                    aria-label="Clear search"
                    title="Clear search filter"
                    @click="clearEligibleSearch"
                  >
                    <i class="fas fa-times"></i>
                  </button>
                  <button type="submit" class="btn btn-default" aria-label="Search" title="Search eligible voters">
                    <i class="fas fa-search"></i>
                  </button>
                </div>
              </form>

              <button type="button" class="btn btn-tool" data-card-widget="collapse" title="Expand or collapse this section" @click="handleEligibleCardToggle">
                <i class="fas fa-plus"></i>
              </button>
            </div>
          </div>

          <div class="card-body" style="display: none;">
            <ElectionCredentialResendControls
              v-if="credentialResendBootstrap"
              class="mb-3"
              :bootstrap="credentialResendBootstrap"
            />

            <WidgetGrid
              :items="eligibleItems"
              :is-loading="isEligibleLoading"
              :error="eligibleError"
              empty-message="No eligible voters."
              :pagination="eligiblePagination"
              :build-page-href="buildEligiblePageHref"
              @page-change="loadEligiblePage"
            >
              <template #item="{ item }">
                <WidgetUser
                  :username="eligibleVoterItem(item).username"
                  :full-name="eligibleVoterItem(item).full_name"
                  :avatar-url="eligibleVoterItem(item).avatar_url || undefined"
                />
              </template>
            </WidgetGrid>
          </div>
        </div>
      </div>
    </div>

    <div class="row">
      <div class="col-12">
        <div id="ineligible-voters-card" class="card card-outline card-warning collapsed-card election-voter-card election-collapsible-card">
          <div class="card-header">
            <h3 class="card-title">Ineligible voters</h3>

            <div class="card-tools">
              <form class="js-card-search input-group input-group-sm" style="width: 220px;" method="get" @submit.prevent="submitIneligibleSearch">
                <input
                  v-model="ineligibleQuery"
                  type="text"
                  name="ineligible_q"
                  class="form-control float-right"
                  placeholder="Search users..."
                  aria-label="Search users"
                >
                <div class="input-group-append">
                  <button
                    v-if="ineligibleQuery"
                    type="button"
                    class="btn btn-default"
                    aria-label="Clear search"
                    title="Clear search filter"
                    @click="clearIneligibleSearch"
                  >
                    <i class="fas fa-times"></i>
                  </button>
                  <button type="submit" class="btn btn-default" aria-label="Search" title="Search ineligible voters">
                    <i class="fas fa-search"></i>
                  </button>
                </div>
              </form>

              <button type="button" class="btn btn-tool" data-card-widget="collapse" title="Expand or collapse this section" @click="handleIneligibleCardToggle">
                <i class="fas fa-plus"></i>
              </button>
            </div>
          </div>

          <div class="card-body" style="display: none;">
            <WidgetGrid
              :items="ineligibleItems"
              :is-loading="isIneligibleLoading"
              :error="ineligibleError"
              empty-message="No ineligible voters found."
              :pagination="ineligiblePagination"
              :build-page-href="buildIneligiblePageHref"
              @page-change="loadIneligiblePage"
            >
              <template #item="{ item }">
                <div @click.capture="handleIneligibleUserClick($event, eligibleVoterItem(item))">
                  <WidgetUser
                    :username="eligibleVoterItem(item).username"
                    :full-name="eligibleVoterItem(item).full_name"
                    :avatar-url="eligibleVoterItem(item).avatar_url || undefined"
                  />
                </div>
              </template>
            </WidgetGrid>
          </div>
        </div>
      </div>
    </div>

    <div
      class="modal fade"
      id="ineligible-voter-modal"
      :class="{ show: isIneligibleModalVisible, 'd-block': isIneligibleModalVisible }"
      tabindex="-1"
      role="dialog"
      aria-labelledby="ineligible-voter-modal-label"
      :aria-hidden="isIneligibleModalVisible ? 'false' : 'true'"
    >
      <div class="modal-dialog" role="document">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title" id="ineligible-voter-modal-label">Ineligible voter details</h5>
            <button type="button" class="close" aria-label="Close" title="Close voter details" @click="hideIneligibleDetails">
              <span aria-hidden="true">&times;</span>
            </button>
          </div>
          <div class="modal-body">
            <div class="mb-2"><strong class="js-ineligible-username">{{ selectedIneligibleUsername }}</strong></div>

            <div class="text-muted small mb-3 js-ineligible-reason">
              {{ selectedIneligibleDetails ? reasonText(selectedIneligibleDetails.reason) : "" }}
            </div>

            <dl class="row mb-0">
              <dt class="col-sm-5">Membership start</dt>
              <dd class="col-sm-7 js-ineligible-term-start">{{ selectedIneligibleDetails ? selectedIneligibleDetails.term_start_date : "" }}</dd>

              <dt class="col-sm-5">Election start</dt>
              <dd class="col-sm-7 js-ineligible-election-start">{{ selectedIneligibleDetails ? selectedIneligibleDetails.election_start_date : "" }}</dd>

              <dt class="col-sm-5">Days of membership at election start</dt>
              <dd class="col-sm-7 js-ineligible-days-at-start">{{ selectedIneligibleDetails ? detailValue(selectedIneligibleDetails.days_at_start) : "" }}</dd>

              <dt class="col-sm-5">Days short</dt>
              <dd class="col-sm-7 js-ineligible-days-short">{{ selectedIneligibleDetails ? detailValue(selectedIneligibleDetails.days_short) : "" }}</dd>
            </dl>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-outline-secondary" title="Close voter details" @click="hideIneligibleDetails">Close</button>
          </div>
        </div>
      </div>
    </div>
    <div v-if="isIneligibleModalVisible" class="modal-backdrop fade show" data-ineligible-voter-backdrop="true"></div>
  </div>
</template>