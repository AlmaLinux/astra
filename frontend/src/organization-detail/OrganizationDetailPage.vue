<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import MembershipCard from "../shared/components/MembershipCard.vue";
import { fillUrlTemplate } from "../shared/urlTemplates";
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
const terminationConfirmByLabel = ref<Record<string, string>>({});

const visibleContactGroup = computed<OrganizationDetailContactGroup | null>(() => {
  if (!payload.value) {
    return null;
  }
  return payload.value.organization.contact_groups.find((group) => group.key === activeContactKey.value) || null;
});

const membershipNotes = computed(() => {
  if (!payload.value || !props.bootstrap.membershipNotes) {
    return null;
  }

  return {
    ...props.bootstrap.membershipNotes,
    targetType: "org",
    target: String(payload.value.organization.id),
  };
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

function requestDetailUrl(requestId: number): string {
  return fillUrlTemplate(props.bootstrap.membershipRequestDetailTemplate, "__request_id__", requestId);
}

function userProfileUrl(username: string): string {
  return fillUrlTemplate(props.bootstrap.userProfileUrlTemplate, "__username__", username);
}

function sendMailUrl(email: string): string {
  return fillUrlTemplate(props.bootstrap.sendMailUrlTemplate, "__email__", email);
}

function membershipTypeCode(membership: OrganizationDetailResponse["organization"]["memberships"][number]): string {
  return membership.membership_type?.code || "";
}

function tierChangeUrl(membership: OrganizationDetailResponse["organization"]["memberships"][number]): string {
  if (!membership.can_request_tier_change || !membership.tier_change_membership_type_code) {
    return "";
  }
  return `${props.bootstrap.membershipRequestUrl}?membership_type=${encodeURIComponent(membership.tier_change_membership_type_code)}`;
}

function canManageExpiration(membership: OrganizationDetailResponse["organization"]["memberships"][number]): boolean {
  return Boolean(membership.can_manage_expiration && membershipTypeCode(membership) && membership.expires_on);
}

function managementModalId(membership: OrganizationDetailResponse["organization"]["memberships"][number]): string {
  const membershipCode = membershipTypeCode(membership);
  return membershipCode ? `sponsorship-expiry-modal-${membershipCode}` : "";
}

function managementInputId(membership: OrganizationDetailResponse["organization"]["memberships"][number]): string {
  const membershipCode = membershipTypeCode(membership);
  return membershipCode ? `sponsorship-expires-on-${membershipCode}` : "";
}

function expiryActionUrl(membership: OrganizationDetailResponse["organization"]["memberships"][number]): string {
  return fillUrlTemplate(props.bootstrap.sponsorshipSetExpiryUrlTemplate, "__membership_type_code__", membershipTypeCode(membership));
}

function terminateActionUrl(membership: OrganizationDetailResponse["organization"]["memberships"][number]): string {
  return fillUrlTemplate(props.bootstrap.sponsorshipTerminateUrlTemplate, "__membership_type_code__", membershipTypeCode(membership));
}

function organizationName(): string {
  return payload.value?.organization.name || "";
}

function expirationCurrentText(membership: OrganizationDetailResponse["organization"]["memberships"][number]): string {
  if (!membership.expires_label) {
    return "";
  }
  return `Current expiration: ${membership.expires_label} (UTC)`;
}

function normalized(value: string): string {
  return value.toLowerCase().trim();
}

function terminationMatches(label: string, terminator: string): boolean {
  return normalized(terminationConfirmByLabel.value[label] || "") === normalized(terminator);
}

function resetTermination(label: string): void {
  terminationConfirmByLabel.value[label] = "";
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
          <div class="d-flex align-items-start flex-wrap" style="gap: 1rem;">
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
          </div>

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
                  <a v-if="payload.organization.representative.username" :href="userProfileUrl(payload.organization.representative.username)">{{ payload.organization.representative.username }}</a>
                  <template v-else>—</template>
                </dd>
              </dl>

              <dl v-else-if="visibleContactGroup" class="row mb-0">
                <dt class="col-sm-4">Name</dt>
                <dd class="col-sm-8">{{ visibleContactGroup.name || '—' }}</dd>
                <dt class="col-sm-4">Email</dt>
                <dd class="col-sm-8">
                  <a v-if="visibleContactGroup.email" :href="sendMailUrl(visibleContactGroup.email)">{{ visibleContactGroup.email }}</a>
                  <template v-else>—</template>
                </dd>
                <dt class="col-sm-4">Phone</dt>
                <dd class="col-sm-8">{{ visibleContactGroup.phone || '—' }}</dd>
              </dl>
            </div>
          </div>
        </div>
      </div>

      <div class="row">
        <div class="col-12">
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
        </div>
      </div>

      <div class="row">
        <div class="col-12">
          <MembershipCard
            :notes="membershipNotes"
            :request-detail-template="bootstrap.membershipRequestDetailTemplate"
          >
            <li
              v-for="membership in payload.organization.memberships"
              :key="membership.label"
              class="list-group-item d-flex justify-content-between align-items-center"
            >
              <div>
                <div class="font-weight-bold">{{ membership.label }}</div>
                <div v-if="membership.description" class="text-muted small">{{ membership.description }}</div>
                <div v-if="membership.member_since_label" class="text-muted small">Member since {{ membership.member_since_label }}</div>
                <div v-if="membership.expires_label" class="small" :class="membership.expires_tone === 'danger' ? 'text-danger' : 'text-muted'">
                  <i v-if="membership.expires_tone === 'danger'" class="fas fa-exclamation-triangle mr-1" />
                  Expires {{ membership.expires_label }}
                </div>
              </div>

              <div class="d-flex align-items-center justify-content-end flex-wrap" style="gap: .5rem;">
                <span :class="`badge alx-status-badge badge-pill p-2 ${membership.class_name} alx-status-badge--active`">{{ membership.label }}</span>
                <a
                  v-if="tierChangeUrl(membership)"
                  :href="tierChangeUrl(membership)"
                  class="btn btn-sm btn-outline-primary"
                  title="Request a change of tier"
                >Change tier</a>
                <button
                  v-if="canManageExpiration(membership)"
                  type="button"
                  class="btn btn-sm btn-outline-secondary"
                  data-toggle="modal"
                  :data-target="`#${managementModalId(membership)}`"
                  title="Edit membership expiration date"
                >Edit expiration</button>
              </div>

              <div
                v-if="canManageExpiration(membership)"
                class="modal fade"
                :id="managementModalId(membership)"
                tabindex="-1"
                role="dialog"
                :aria-labelledby="`${managementModalId(membership)}-label`"
                aria-hidden="true"
              >
                <div class="modal-dialog" role="document">
                  <div class="modal-content">
                    <div class="modal-header">
                      <div class="mr-3">
                        <h5 class="modal-title mb-0" :id="`${managementModalId(membership)}-label`">Manage membership: {{ membership.membership_type?.name || membership.label }} for {{ organizationName() }}</h5>
                      </div>
                      <button type="button" class="close" data-dismiss="modal" aria-label="Close" title="Close dialog">
                        <span aria-hidden="true">&times;</span>
                      </button>
                    </div>

                    <div class="modal-body">
                      <div v-if="expirationCurrentText(membership)" class="mb-3">{{ expirationCurrentText(membership) }}</div>

                      <form method="post" :action="expiryActionUrl(membership)" novalidate>
                        <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
                        <input v-if="bootstrap.nextUrl" type="hidden" name="next" :value="bootstrap.nextUrl">

                        <div class="form-group">
                          <label :for="managementInputId(membership)">Expiration date</label>
                          <input
                            :id="managementInputId(membership)"
                            name="expires_on"
                            type="date"
                            class="form-control"
                            :value="membership.expires_on"
                            :min="bootstrap.expiryMinDate"
                            required
                          >
                        </div>

                        <div class="text-muted small mb-3">Expiration is an end-of-day date in UTC.</div>

                        <div class="d-flex justify-content-between">
                          <button type="button" class="btn btn-secondary" data-dismiss="modal" aria-label="Cancel" title="Close dialog without saving">Cancel</button>
                          <button type="submit" class="btn btn-primary" title="Save expiration date">Save expiration</button>
                        </div>
                      </form>

                      <div class="mt-4 pt-3 border-top">
                        <div class="border border-danger rounded p-3 bg-light">
                          <div class="font-weight-bold text-danger mb-1">Danger zone</div>
                          <div class="small text-muted mb-3">Ends this membership early.</div>

                          <div class="d-flex justify-content-end">
                            <button
                              type="button"
                              class="btn btn-outline-danger"
                              data-toggle="collapse"
                              :data-target="`#${managementModalId(membership)}-terminate-collapse`"
                              aria-expanded="false"
                              :aria-controls="`${managementModalId(membership)}-terminate-collapse`"
                              title="Show termination confirmation"
                            >Terminate membership&hellip;</button>
                          </div>

                          <div class="collapse mt-3" :id="`${managementModalId(membership)}-terminate-collapse`">
                            <div class="alert alert-danger mb-3" role="alert">This will end the membership early and cannot be undone.</div>

                            <form method="post" :action="terminateActionUrl(membership)" class="m-0" novalidate>
                              <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
                              <input v-if="bootstrap.nextUrl" type="hidden" name="next" :value="bootstrap.nextUrl">

                              <div class="form-group">
                                <label :for="`${managementModalId(membership)}-terminate-confirm-input`">Type the name to confirm</label>
                                <input
                                  :id="`${managementModalId(membership)}-terminate-confirm-input`"
                                  v-model="terminationConfirmByLabel[membership.label]"
                                  name="confirm"
                                  type="text"
                                  class="form-control"
                                  :class="{
                                    'is-valid': Boolean(terminationConfirmByLabel[membership.label]) && terminationMatches(membership.label, organizationName()),
                                    'is-invalid': Boolean(terminationConfirmByLabel[membership.label]) && !terminationMatches(membership.label, organizationName()),
                                  }"
                                  :placeholder="organizationName()"
                                  autocomplete="off"
                                  :data-terminate-target="organizationName()"
                                  required
                                >
                                <div class="invalid-feedback">Does not match. Type the name to enable termination (case-insensitive).</div>
                              </div>

                              <div class="d-flex justify-content-end">
                                <button type="button" class="btn btn-outline-secondary" data-terminate-action="cancel" title="Cancel termination and collapse this section" @click="resetTermination(membership.label)">Cancel termination</button>
                                <button
                                  type="submit"
                                  class="btn btn-danger ml-2"
                                  :id="`${managementModalId(membership)}-terminate-submit`"
                                  :disabled="!terminationMatches(membership.label, organizationName())"
                                  :aria-disabled="terminationMatches(membership.label, organizationName()) ? 'false' : 'true'"
                                  title="Terminate membership (enabled after confirmation)"
                                >Terminate membership</button>
                              </div>
                            </form>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </li>

            <li
              v-for="pendingMembership in payload.organization.pending_memberships"
              :key="pendingMembership.request_id"
              class="list-group-item d-flex justify-content-between align-items-center"
            >
              <div>
                <div class="font-weight-bold">{{ pendingMembership.membership_type.name }}</div>
                <div class="small text-muted">
                  <a :href="requestDetailUrl(pendingMembership.request_id)">Request #{{ pendingMembership.request_id }}</a>
                </div>
                <div v-if="pendingMembership.membership_type.description" class="small text-muted">
                  {{ pendingMembership.membership_type.description }}
                </div>
              </div>
              <span :class="pendingMembership.badge_class_name">{{ pendingMembership.badge_label }}</span>
            </li>

            <li v-if="!payload.organization.memberships.length && !payload.organization.pending_memberships.length" class="list-group-item text-muted">
              No membership selected
            </li>
          </MembershipCard>
        </div>
      </div>
    </template>
  </div>
</template>
