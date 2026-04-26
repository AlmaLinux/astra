<script setup lang="ts">
import { onMounted, ref } from "vue";

import WidgetGrid from "../shared/components/WidgetGrid.vue";
import WidgetOrganization from "../shared/components/WidgetOrganization.vue";
import { fillUrlTemplate } from "../shared/urlTemplates";
import {
  buildOrganizationsRouteUrl,
  readOrganizationsRouteState,
  type OrganizationCardItem,
  type OrganizationsBootstrap,
  type OrganizationsCardPayload,
  type OrganizationsResponse,
  type OrganizationsRouteState,
} from "./types";

const props = defineProps<{
  bootstrap: OrganizationsBootstrap;
}>();

const myOrganization = ref<OrganizationCardItem | null>(null);
const sponsorCard = ref<OrganizationsCardPayload | null>(null);
const mirrorCard = ref<OrganizationsCardPayload | null>(null);
const isSponsorLoading = ref(false);
const isMirrorLoading = ref(false);
const error = ref("");
const sponsorQ = ref("");
const mirrorQ = ref("");
const sponsorPage = ref(1);
const mirrorPage = ref(1);

function currentRouteState(): OrganizationsRouteState {
  return {
    pathname: window.location.pathname,
    qSponsor: sponsorQ.value,
    qMirror: mirrorQ.value,
    pageSponsor: sponsorPage.value,
    pageMirror: mirrorPage.value,
  };
}

function applyRouteState(routeState: OrganizationsRouteState): void {
  sponsorQ.value = routeState.qSponsor;
  mirrorQ.value = routeState.qMirror;
  sponsorPage.value = routeState.pageSponsor;
  mirrorPage.value = routeState.pageMirror;
}

function syncUrl(pushState: boolean): void {
  const nextUrl = buildOrganizationsRouteUrl(currentRouteState());
  if (pushState) {
    window.history.pushState(null, "", nextUrl);
    return;
  }
  window.history.replaceState(null, "", nextUrl);
}

type OrganizationsLoadTarget = "all" | "sponsor" | "mirror";

function organizationDetailHref(organizationId: number): string {
  return fillUrlTemplate(props.bootstrap.detailUrlTemplate, "__organization_id__", organizationId);
}

async function load(target: OrganizationsLoadTarget, pushState: boolean): Promise<void> {
  if (target === "all" || target === "sponsor") {
    isSponsorLoading.value = true;
  }
  if (target === "all" || target === "mirror") {
    isMirrorLoading.value = true;
  }
  error.value = "";

  try {
    const routeUrl = buildOrganizationsRouteUrl(currentRouteState());
    const query = routeUrl.includes("?") ? routeUrl.slice(routeUrl.indexOf("?")) : "";
    const response = await fetch(`${props.bootstrap.apiUrl}${query}`, {
      headers: {
        Accept: "application/json",
      },
      credentials: "same-origin",
    });
    if (!response.ok) {
      error.value = "Unable to load organizations right now.";
      return;
    }

    const payload = (await response.json()) as OrganizationsResponse;
    if (target === "all") {
      myOrganization.value = payload.my_organization;
      sponsorCard.value = payload.sponsor_card;
      mirrorCard.value = payload.mirror_card;
      sponsorQ.value = payload.sponsor_card.q;
      mirrorQ.value = payload.mirror_card.q;
    } else if (target === "sponsor") {
      sponsorCard.value = payload.sponsor_card;
      sponsorQ.value = payload.sponsor_card.q;
    } else {
      mirrorCard.value = payload.mirror_card;
      mirrorQ.value = payload.mirror_card.q;
    }
    syncUrl(pushState);
  } catch {
    error.value = "Unable to load organizations right now.";
  } finally {
    if (target === "all" || target === "sponsor") {
      isSponsorLoading.value = false;
    }
    if (target === "all" || target === "mirror") {
      isMirrorLoading.value = false;
    }
  }
}

function buildPageHref(card: "sponsor" | "mirror", pageNumber: number): string {
  const routeState = currentRouteState();
  if (card === "sponsor") {
    routeState.pageSponsor = pageNumber;
  } else {
    routeState.pageMirror = pageNumber;
  }
  return buildOrganizationsRouteUrl(routeState);
}

async function onSponsorPageChange(pageNumber: number): Promise<void> {
  sponsorPage.value = pageNumber;
  await load("sponsor", true);
}

async function onMirrorPageChange(pageNumber: number): Promise<void> {
  mirrorPage.value = pageNumber;
  await load("mirror", true);
}

async function onSponsorSearch(): Promise<void> {
  sponsorPage.value = 1;
  await load("sponsor", true);
}

async function onMirrorSearch(): Promise<void> {
  mirrorPage.value = 1;
  await load("mirror", true);
}

function clearSponsorSearch(): void {
  sponsorQ.value = "";
  void onSponsorSearch();
}

function clearMirrorSearch(): void {
  mirrorQ.value = "";
  void onMirrorSearch();
}

onMounted(async () => {
  applyRouteState(readOrganizationsRouteState(window.location.href));
  window.addEventListener("popstate", () => {
    applyRouteState(readOrganizationsRouteState(window.location.href));
    void load("all", false);
  });
  await load("all", false);
});
</script>

<template>
  <div data-organizations-vue-root>
    <div class="card">
      <div class="card-header">
        <h3 class="card-title mb-0">My Organization</h3>
      </div>
      <div class="card-body">
        <div class="alert alert-info">
          Create an organization profile only if you are an employee or authorized representative of the organization applying to sponsor AlmaLinux.
        </div>

        <WidgetOrganization
          v-if="myOrganization"
          :name="myOrganization.name"
          :status="myOrganization.status"
          :detail-url="organizationDetailHref(myOrganization.id)"
          :logo-url="myOrganization.logo_url"
          :link-to-detail="myOrganization.link_to_detail"
          :memberships="myOrganization.memberships"
        />
        <a v-else :href="bootstrap.createUrl" class="btn btn-primary" title="Create a new organization">Create organization</a>
      </div>
    </div>

    <div v-if="error" class="text-muted mb-3">{{ error }}</div>

    <div class="card">
      <div class="card-header">
        <div class="d-flex align-items-center justify-content-between" style="gap: .75rem;">
          <h3 class="card-title mb-0">{{ sponsorCard?.title || 'AlmaLinux Sponsor Members' }}</h3>
          <form method="get" class="input-group input-group-sm" style="width: 220px;" @submit.prevent="onSponsorSearch">
            <input
              v-model="sponsorQ"
              type="text"
              name="q_sponsor"
              class="form-control float-right"
              placeholder="Search"
              aria-label="Search sponsor organizations"
            >
            <div class="input-group-append">
              <button v-if="sponsorQ" type="button" class="btn btn-default" aria-label="Clear search" title="Clear search filter" @click="clearSponsorSearch">
                <i class="fas fa-times" />
              </button>
              <button type="submit" class="btn btn-default" aria-label="Search" title="Search sponsor organizations">
                <i class="fas fa-search" />
              </button>
            </div>
          </form>
        </div>
      </div>
      <div class="card-body">
        <WidgetGrid
          :items="sponsorCard?.items || []"
          :is-loading="isSponsorLoading"
          :error="''"
          :empty-message="sponsorCard?.empty_label || 'No AlmaLinux sponsor members found.'"
          :pagination="sponsorCard?.pagination || null"
          :build-page-href="(pageNumber) => buildPageHref('sponsor', pageNumber)"
          @page-change="onSponsorPageChange"
        >
          <template #item="{ item }">
            <WidgetOrganization
              :name="(item as OrganizationCardItem).name"
              :status="(item as OrganizationCardItem).status"
              :detail-url="organizationDetailHref((item as OrganizationCardItem).id)"
              :logo-url="(item as OrganizationCardItem).logo_url"
              :link-to-detail="(item as OrganizationCardItem).link_to_detail"
              :memberships="(item as OrganizationCardItem).memberships"
            />
          </template>
        </WidgetGrid>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div class="d-flex align-items-center justify-content-between" style="gap: .75rem;">
          <h3 class="card-title mb-0">{{ mirrorCard?.title || 'Mirror Sponsor Members' }}</h3>
          <form method="get" class="input-group input-group-sm" style="width: 220px;" @submit.prevent="onMirrorSearch">
            <input
              v-model="mirrorQ"
              type="text"
              name="q_mirror"
              class="form-control float-right"
              placeholder="Search"
              aria-label="Search mirror organizations"
            >
            <div class="input-group-append">
              <button v-if="mirrorQ" type="button" class="btn btn-default" aria-label="Clear search" title="Clear search filter" @click="clearMirrorSearch">
                <i class="fas fa-times" />
              </button>
              <button type="submit" class="btn btn-default" aria-label="Search" title="Search mirror organizations">
                <i class="fas fa-search" />
              </button>
            </div>
          </form>
        </div>
      </div>
      <div class="card-body">
        <WidgetGrid
          :items="mirrorCard?.items || []"
          :is-loading="isMirrorLoading"
          :error="''"
          :empty-message="mirrorCard?.empty_label || 'No mirror sponsor members found.'"
          :pagination="mirrorCard?.pagination || null"
          :build-page-href="(pageNumber) => buildPageHref('mirror', pageNumber)"
          @page-change="onMirrorPageChange"
        >
          <template #item="{ item }">
            <WidgetOrganization
              :name="(item as OrganizationCardItem).name"
              :status="(item as OrganizationCardItem).status"
              :detail-url="organizationDetailHref((item as OrganizationCardItem).id)"
              :logo-url="(item as OrganizationCardItem).logo_url"
              :link-to-detail="(item as OrganizationCardItem).link_to_detail"
              :memberships="(item as OrganizationCardItem).memberships"
            />
          </template>
        </WidgetGrid>
      </div>
    </div>
  </div>
</template>
