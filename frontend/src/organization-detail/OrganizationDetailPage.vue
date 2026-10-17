<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import MembershipCard from "../shared/components/MembershipCard.vue";
import MembershipManageModal from "../shared/components/MembershipManageModal.vue";
import { formatFullDate, formatUsd, pendingMembershipBadge } from "../shared/membershipPresentation";
import { fillUrlTemplate } from "../shared/urlTemplates";
import type {
  OrganizationDetailBootstrap,
  OrganizationDetailContactGroup,
  OrganizationDetailMembership,
  OrganizationDetailMembershipType,
  OrganizationDetailResponse,
} from "./types";

const props = defineProps<{
  bootstrap: OrganizationDetailBootstrap;
}>();

const payload = ref<OrganizationDetailResponse | null>(null);
const error = ref("");
const isLoading = ref(false);
const activeContactKey = ref("representative");

const CONTACT_LABELS: Record<string, string> = {
  business: "Business",
  marketing: "PR and marketing",
  technical: "Technical",
};

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
  return membership.membershipType.code || "";
}

function membershipRequestActionUrl(membershipTypeCodeValue: string): string {
  if (!membershipTypeCodeValue) {
    return "";
  }
  return `${props.bootstrap.membershipRequestUrl}?membership_type=${encodeURIComponent(membershipTypeCodeValue)}`;
}

function renewalUrl(membership: OrganizationDetailResponse["organization"]["memberships"][number]): string {
  if (!membership.canRenew) {
    return "";
  }
  return membershipRequestActionUrl(membership.renewalMembershipTypeCode);
}

function tierChangeUrl(membership: OrganizationDetailResponse["organization"]["memberships"][number]): string {
  if (!membership.canRequestTierChange) {
    return "";
  }
  return membershipRequestActionUrl(membership.tierChangeMembershipTypeCode);
}

function canManageExpiration(membership: OrganizationDetailResponse["organization"]["memberships"][number]): boolean {
  return Boolean(membership.canManage && membershipTypeCode(membership));
}

function managementModalId(membership: OrganizationDetailResponse["organization"]["memberships"][number]): string {
  const membershipCode = membershipTypeCode(membership);
  return membershipCode ? `sponsorship-expiry-modal-${membershipCode}` : "";
}

function updateActionUrl(membership: OrganizationDetailMembership): string {
  return fillUrlTemplate(props.bootstrap.membershipUpdateUrlTemplate, "__membership_id__", membership.id);
}

function terminateActionUrl(membership: OrganizationDetailMembership): string {
  return fillUrlTemplate(props.bootstrap.membershipTerminateUrlTemplate, "__membership_id__", membership.id);
}

// The page's only statement of a paid tier's dues: descriptions are shown without them.
// Pending requests have no dues yet (null), so they show the standard price.
function duesLabel(membershipType: OrganizationDetailMembershipType, dues: number | null): string {
  const defaultDues = membershipType.annualDuesUsd;
  if (!(defaultDues > 0)) {
    return "";
  }
  if (dues === null || dues >= defaultDues) {
    return `Annual dues: ${formatUsd(defaultDues)}`;
  }
  if (dues === 0) {
    return "In-kind sponsor: dues waived";
  }
  return `Annual dues: ${formatUsd(dues)} (discounted from ${formatUsd(defaultDues)})`;
}

function invoiceModalId(membership: OrganizationDetailMembership): string {
  return `sponsorship-invoice-modal-${membershipTypeCode(membership)}`;
}

function invoiceActionLabel(membership: OrganizationDetailMembership): string {
  return membership.invoice?.url ? "Regenerate invoice" : "Generate invoice";
}

function invoiceRegenerateActionUrl(membership: OrganizationDetailMembership): string {
  return fillUrlTemplate(props.bootstrap.sponsorshipInvoiceRegenerateUrlTemplate, "__membership_type_code__", membershipTypeCode(membership));
}

function organizationName(): string {
  return payload.value?.organization.name || "";
}

function membershipLabel(membership: OrganizationDetailMembership): string {
  return membership.membershipType.name;
}

function memberSinceLabel(membership: OrganizationDetailMembership): string {
  return formatFullDate(membership.createdAt);
}

function expiresLabel(membership: OrganizationDetailMembership): string {
  return formatFullDate(membership.expiresAt);
}

function expiresToneClass(membership: OrganizationDetailMembership): string {
  return membership.isExpiringSoon ? "text-danger" : "text-muted";
}

function contactLabel(key: string): string {
  return CONTACT_LABELS[key] || key;
}

function pendingBadge(status: string): { label: string; className: string } {
  return pendingMembershipBadge(status, Boolean(payload.value?.organization.is_representative));
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
                  <button type="button" class="nav-link" :class="{ active: activeContactKey === group.key }" @click="activeContactKey = group.key">{{ contactLabel(group.key) }}</button>
                </li>
              </ul>
            </div>

            <div class="card-body">
              <dl v-if="activeContactKey === 'representative'" class="row mb-0">
                <dt class="col-sm-4">Name</dt>
                <dd class="col-sm-8">{{ payload.organization.representative.full_name || 'Not provided.' }}</dd>
                <dt class="col-sm-4">Username</dt>
                <dd class="col-sm-8">
                  <a v-if="payload.organization.representative.username" :href="userProfileUrl(payload.organization.representative.username)">{{ payload.organization.representative.username }}</a>
                  <template v-else>Not provided.</template>
                </dd>
              </dl>

              <dl v-else-if="visibleContactGroup" class="row mb-0">
                <dt class="col-sm-4">Name</dt>
                <dd class="col-sm-8">{{ visibleContactGroup.name || 'Not provided.' }}</dd>
                <dt class="col-sm-4">Email</dt>
                <dd class="col-sm-8">
                  <a v-if="visibleContactGroup.email" :href="sendMailUrl(visibleContactGroup.email)">{{ visibleContactGroup.email }}</a>
                  <template v-else>Not provided.</template>
                </dd>
                <dt class="col-sm-4">Phone</dt>
                <dd class="col-sm-8">{{ visibleContactGroup.phone || 'Not provided.' }}</dd>
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
                    <div v-else class="text-muted">Not provided.</div>
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

                    <div v-if="payload.organization.billing_notes" class="mt-3">
                      <div class="small font-weight-bold text-muted">Billing notes</div>
                      <div style="white-space: pre-line;">{{ payload.organization.billing_notes }}</div>
                    </div>
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
            <template #actions>
              <a
                v-if="payload.organization.can_view_history"
                :href="bootstrap.membershipHistoryUrl"
                class="btn btn-sm btn-outline-secondary"
                title="View membership history"
              >History</a>
              <a
                v-if="payload.organization.can_request_membership && payload.organization.can_request_any"
                :href="bootstrap.membershipRequestUrl"
                class="btn btn-sm btn-outline-primary"
                title="Request membership"
              >Request membership</a>
            </template>

            <li
              v-for="membership in payload.organization.memberships"
              :key="membership.key"
              class="list-group-item d-flex justify-content-between align-items-center"
            >
              <div>
                <div class="font-weight-bold">{{ membershipLabel(membership) }}</div>
                <div v-if="membership.membershipType.description" class="text-muted small">{{ membership.membershipType.description }}</div>
                <div v-if="memberSinceLabel(membership)" class="text-muted small">Member since {{ memberSinceLabel(membership) }}</div>
                <div v-if="expiresLabel(membership)" class="small" :class="expiresToneClass(membership)">
                  <i v-if="membership.isExpiringSoon" class="fas fa-exclamation-triangle mr-1" />
                  Expires {{ expiresLabel(membership) }}
                </div>
                <div v-if="duesLabel(membership.membershipType, membership.duesUsd)" class="small" :data-membership-dues="membershipTypeCode(membership)">
                  <i class="fas fa-hand-holding-dollar mr-1" />{{ duesLabel(membership.membershipType, membership.duesUsd) }}
                </div>
                <div v-if="membership.invoice?.url" class="small">
                  <a
                    :href="membership.invoice.url"
                    target="_blank"
                    rel="noopener"
                    title="Download the invoice (PDF)"
                  ><i class="fas fa-file-pdf mr-1" />Download invoice {{ membership.invoice.number }}</a>
                </div>
              </div>

              <div class="d-flex flex-column align-items-end" style="gap: .5rem;">
                <div
                  v-if="renewalUrl(membership) || tierChangeUrl(membership) || membership.invoice"
                  class="d-flex align-items-center justify-content-end flex-wrap"
                  style="gap: .5rem;"
                  data-membership-actions="representative"
                >
                  <a
                    v-if="renewalUrl(membership)"
                    :href="renewalUrl(membership)"
                    class="btn btn-sm btn-primary"
                    title="Request renewal for this membership"
                  >Request renewal</a>
                  <a
                    v-if="tierChangeUrl(membership)"
                    :href="tierChangeUrl(membership)"
                    class="btn btn-sm btn-outline-primary"
                    title="Request a change of tier"
                  >Change tier</a>
                  <button
                    v-if="membership.invoice"
                    type="button"
                    class="btn btn-sm btn-outline-secondary"
                    data-toggle="modal"
                    :data-target="`#${invoiceModalId(membership)}`"
                    title="Rebuild the renewal invoice from the current organization details"
                  >{{ invoiceActionLabel(membership) }}</button>
                </div>

                <div
                  v-if="canManageExpiration(membership)"
                  class="d-flex align-items-center justify-content-end flex-wrap"
                  style="gap: .5rem;"
                  data-membership-actions="committee"
                >
                  <button
                    type="button"
                    class="btn btn-sm btn-outline-secondary"
                    data-toggle="modal"
                    :data-target="`#${managementModalId(membership)}`"
                    title="Edit or terminate this membership"
                  >Edit membership</button>
                </div>
              </div>

              <div
                v-if="membership.invoice"
                class="modal fade"
                :id="invoiceModalId(membership)"
                tabindex="-1"
                role="dialog"
                :aria-labelledby="`${invoiceModalId(membership)}-label`"
                aria-hidden="true"
              >
                <div class="modal-dialog" role="document">
                  <div class="modal-content">
                    <form method="post" :action="invoiceRegenerateActionUrl(membership)" class="m-0">
                      <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">

                      <div class="modal-header">
                        <h5 class="modal-title mb-0" :id="`${invoiceModalId(membership)}-label`">{{ invoiceActionLabel(membership) }}: {{ membershipLabel(membership) }} for {{ organizationName() }}</h5>
                        <button type="button" class="close" data-dismiss="modal" aria-label="Close" title="Close dialog">
                          <span aria-hidden="true">&times;</span>
                        </button>
                      </div>

                      <div class="modal-body">
                        The invoice will be rebuilt from the organization's current name, address and billing notes.
                        Its download link stays the same.
                      </div>

                      <div class="modal-footer d-flex justify-content-between">
                        <button type="button" class="btn btn-secondary" data-dismiss="modal" title="Close dialog without generating">Cancel</button>
                        <button type="submit" class="btn btn-primary" title="Generate the invoice now">{{ invoiceActionLabel(membership) }}</button>
                      </div>
                    </form>
                  </div>
                </div>
              </div>

              <MembershipManageModal
                v-if="canManageExpiration(membership)"
                :modal-id="managementModalId(membership)"
                :membership-type="membership.membershipType"
                :target-name="organizationName()"
                target-noun="organization name"
                :expires-at="membership.expiresAt"
                :expires-label="expiresLabel(membership)"
                :expiry-min-date="bootstrap.expiryMinDate"
                :dues-usd="membership.duesUsd"
                :update-url="updateActionUrl(membership)"
                :terminate-url="terminateActionUrl(membership)"
                :csrf-token="bootstrap.csrfToken"
                :next-url="bootstrap.nextUrl"
              />
            </li>

            <li
              v-for="pendingMembership in payload.organization.pending_memberships"
              :key="pendingMembership.key"
              class="list-group-item d-flex justify-content-between align-items-center"
            >
              <div>
                <div class="font-weight-bold">{{ pendingMembership.membershipType.name }}</div>
                <div class="small text-muted">
                  <a :href="requestDetailUrl(pendingMembership.requestId)">Request #{{ pendingMembership.requestId }}</a>
                </div>
                <div v-if="pendingMembership.membershipType.description" class="small text-muted">
                  {{ pendingMembership.membershipType.description }}
                </div>
                <div v-if="duesLabel(pendingMembership.membershipType, null)" class="small">
                  <i class="fas fa-hand-holding-dollar mr-1" />{{ duesLabel(pendingMembership.membershipType, null) }}
                </div>
              </div>
              <span :class="pendingBadge(pendingMembership.status).className">{{ pendingBadge(pendingMembership.status).label }}</span>
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
