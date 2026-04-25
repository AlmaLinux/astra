<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import MembershipNotesCard from "../membership-requests/components/MembershipNotesCard.vue";
import type {
  OrganizationDetailBootstrap,
  OrganizationDetailContactGroup,
  OrganizationDetailResponse,
} from "./types";

const props = defineProps<{
  bootstrap: OrganizationDetailBootstrap;
}>();

const payload = ref<OrganizationDetailResponse | null>(null);
const error = ref("");
const isLoading = ref(false);
const activeContactKey = ref("representative");

const visibleContactGroup = computed<OrganizationDetailContactGroup | null>(() => {
  if (!payload.value) {
    return null;
  }
  return payload.value.organization.contact_groups.find((group) => group.key === activeContactKey.value) || null;
});

function addressLines(): string[] {
  if (!payload.value) {
    return [];
  }

  const address = payload.value.organization.address;
  const lines: string[] = [];
  if (address.street) {
    lines.push(address.street);
  }

  const cityLine = [address.city, address.state, address.postal_code].filter(Boolean).join(address.city && address.state ? ", " : " ").trim();
  if (cityLine) {
    lines.push(cityLine);
  }
  if (address.country_code) {
    lines.push(address.country_code);
  }
  return lines;
}

async function load(): Promise<void> {
  isLoading.value = true;
  error.value = "";

  try {
    const response = await fetch(props.bootstrap.apiUrl, {
      headers: {
        Accept: "application/json",
      },
      credentials: "same-origin",
    });
    if (!response.ok) {
      error.value = "Unable to load organization details right now.";
      return;
    }
    payload.value = (await response.json()) as OrganizationDetailResponse;
  } catch {
    error.value = "Unable to load organization details right now.";
  } finally {
    isLoading.value = false;
  }
}

onMounted(async () => {
  await load();
});
</script>

<template>
  <div data-organization-detail-vue-root>
    <div v-if="error" class="text-muted mb-3">{{ error }}</div>
    <div v-else-if="isLoading && !payload" class="text-muted mb-3">Loading organization details...</div>
    <template v-else-if="payload">
      <div class="card organization-hero">
        <div v-if="payload.organization.status === 'unclaimed'" class="ribbon-wrapper ribbon-lg">
          <div class="ribbon bg-warning">Unclaimed</div>
        </div>
        <div class="card-body">
          <div class="d-flex align-items-start justify-content-between flex-wrap" style="gap: 1rem;">
            <div class="d-flex align-items-center" style="gap: 1rem;">
              <div class="organization-hero-logo">
                <img v-if="payload.organization.logo_url" :src="payload.organization.logo_url" :alt="payload.organization.name">
                <div v-else class="organization-hero-logo--placeholder" aria-hidden="true">
                  <i class="fas fa-building" />
                </div>
              </div>

              <div>
                <div class="h4 mb-1">{{ payload.organization.name }}</div>
                <div v-if="payload.organization.website" class="text-muted small">
                  <a :href="payload.organization.website" rel="noopener noreferrer">{{ payload.organization.website }}</a>
                </div>
              </div>
            </div>

            <div class="d-flex align-items-center justify-content-end flex-wrap" style="gap: .5rem;">
              <span
                v-for="membership in payload.organization.memberships"
                :key="membership.label"
                :class="`badge alx-status-badge badge-pill p-2 ${membership.class_name} alx-status-badge--active`"
              >{{ membership.label }}</span>
              <span v-if="!payload.organization.memberships.length" class="badge badge-light alx-status-badge alx-status-badge--empty">No membership selected</span>
            </div>
          </div>

        </div>
      </div>

      <div v-if="payload.organization.notes" class="card organization-notes-card">
        <div class="card-header">
          <h3 class="card-title mb-0">
            <i class="fas fa-sticky-note mr-1 text-muted" />
            Membership notes
          </h3>
        </div>
        <div class="card-body">
          <MembershipNotesCard
            :request-id="0"
            :summary-url="payload.organization.notes.summaryUrl"
            :detail-url="payload.organization.notes.detailUrl"
            :add-url="payload.organization.notes.addUrl"
            :csrf-token="payload.organization.notes.csrfToken"
            :next-url="payload.organization.notes.nextUrl"
            :can-view="payload.organization.notes.canView"
            :can-write="payload.organization.notes.canWrite"
            :can-vote="false"
            :target-type="payload.organization.notes.targetType"
            :target="payload.organization.notes.target"
          />
        </div>
      </div>

      <div class="row">
        <div class="col-12">
          <div class="card card-primary card-outline card-outline-tabs">
            <div class="card-header p-0 border-bottom-0">
              <ul class="nav nav-tabs" role="tablist">
                <li class="pt-2 px-3"><h3 class="card-title">Contacts</h3></li>
                <li class="nav-item">
                  <button type="button" class="nav-link" :class="{ active: activeContactKey === 'representative' }" @click="activeContactKey = 'representative'">Representative</button>
                </li>
                <li v-for="group in payload.organization.contact_groups" :key="group.key" class="nav-item">
                  <button type="button" class="nav-link" :class="{ active: activeContactKey === group.key }" @click="activeContactKey = group.key">{{ group.label }}</button>
                </li>
              </ul>
            </div>

            <div class="card-body">
              <dl v-if="activeContactKey === 'representative'" class="row mb-0">
                <dt class="col-sm-4">Name</dt>
                <dd class="col-sm-8">{{ payload.organization.representative.full_name || '—' }}</dd>
                <dt class="col-sm-4">Username</dt>
                <dd class="col-sm-8">
                  <a v-if="payload.organization.representative.username" :href="`/user/${payload.organization.representative.username}/`">{{ payload.organization.representative.username }}</a>
                  <template v-else>—</template>
                </dd>
              </dl>

              <dl v-else-if="visibleContactGroup" class="row mb-0">
                <dt class="col-sm-4">Name</dt>
                <dd class="col-sm-8">{{ visibleContactGroup.name || '—' }}</dd>
                <dt class="col-sm-4">Email</dt>
                <dd class="col-sm-8">
                  <a v-if="visibleContactGroup.email" :href="`/mail/send/?type=manual&to=${encodeURIComponent(visibleContactGroup.email)}`">{{ visibleContactGroup.email }}</a>
                  <template v-else>—</template>
                </dd>
                <dt class="col-sm-4">Phone</dt>
                <dd class="col-sm-8">{{ visibleContactGroup.phone || '—' }}</dd>
              </dl>
            </div>
          </div>
        </div>
      </div>

      <div class="card organization-info-card">
        <div class="card-header">
          <h3 class="card-title mb-0">
            <i class="fas fa-info-circle mr-1 text-muted" />
            Organization information
          </h3>
        </div>
        <div class="card-body">
          <div class="row">
            <div class="col-12 col-lg-6 mb-3 mb-lg-0">
              <div class="p-3 bg-light rounded border h-100">
                <div class="d-flex align-items-center mb-3">
                  <i class="fas fa-link text-muted mr-2" />
                  <div class="h6 mb-0 font-weight-bold">Website</div>
                </div>
                <div v-if="payload.organization.website" class="small">
                  <a :href="payload.organization.website" rel="noopener noreferrer">{{ payload.organization.website }}</a>
                </div>
                <div v-else class="text-muted">—</div>
              </div>
            </div>
            <div class="col-12 col-lg-6">
              <div class="p-3 bg-light rounded border h-100">
                <div class="d-flex align-items-center mb-3">
                  <i class="fas fa-map-marker-alt text-muted mr-2" />
                  <div class="h6 mb-0 font-weight-bold">Address</div>
                </div>
                <address v-if="addressLines().length" class="mb-0">
                  <div v-for="line in addressLines()" :key="line">{{ line }}</div>
                </address>
                <div v-else class="text-muted">No address provided.</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
