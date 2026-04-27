<script setup lang="ts">
import { computed, ref } from "vue";

import MembershipCard from "../shared/components/MembershipCard.vue";
import { formatDateInputValue, formatMonthYear, formatPreciseDateTime, formatShortDate, membershipTierClass, pendingMembershipBadge } from "../shared/membershipPresentation";
import { fillUrlTemplate } from "../shared/urlTemplates";
import type {
  UserProfileMembershipEntry,
  UserProfileMembershipManagementAction,
  UserProfileMembershipNotes,
  UserProfileMembershipSection,
} from "./types";

const props = defineProps<{
  membership: UserProfileMembershipSection;
  timezoneName: string;
  membershipHistoryUrlTemplate: string;
  membershipRequestUrl: string;
  membershipRequestDetailUrlTemplate: string;
  membershipManagement: UserProfileMembershipManagementAction;
  membershipNotes: UserProfileMembershipNotes;
}>();

const notes = computed(() => {
  if (!props.membershipNotes.canView) {
    return null;
  }

  return {
    ...props.membershipNotes,
    targetType: "user",
    target: props.membership.username,
  };
});

function requestDetailUrl(requestId: number | null): string {
  if (requestId === null) {
    return "";
  }

  return fillUrlTemplate(props.membershipRequestDetailUrlTemplate, "__request_id__", requestId);
}

function historyUrl(username: string): string {
  return fillUrlTemplate(props.membershipHistoryUrlTemplate, "__username__", username);
}

function membershipRequestActionUrl(membershipTypeCode: string): string {
  const url = new URL(props.membershipRequestUrl, window.location.origin);
  url.searchParams.set("membership_type", membershipTypeCode);
  return `${url.pathname}${url.search}`;
}

function badgeTag(requestId: number | null): "a" | "span" {
  return requestId === null ? "span" : "a";
}

function membershipBadgeClass(entry: UserProfileMembershipEntry): string {
  return `badge alx-status-badge ${membershipTierClass(entry.membershipType.code)} alx-status-badge--active`;
}

function memberSinceLabel(entry: UserProfileMembershipEntry): string {
  return formatMonthYear(entry.createdAt);
}

function expiresLabel(entry: UserProfileMembershipEntry): string {
  if (entry.isExpiringSoon) {
    return formatPreciseDateTime(entry.expiresAt, props.timezoneName);
  }
  return formatShortDate(entry.expiresAt);
}

function expiresToneClass(entry: UserProfileMembershipEntry): string {
  return entry.isExpiringSoon ? "text-danger" : "text-muted";
}

function pendingBadge(status: string): { label: string; className: string } {
  return pendingMembershipBadge(status, props.membership.isOwner);
}

function managementModalId(index: number): string {
  return `expiry-modal-${index}`;
}

function managementInputId(index: number): string {
  return `expires-on-${index}`;
}

function managementCollapseId(index: number): string {
  return `${managementModalId(index)}-terminate-collapse`;
}

function managementTerminationInputId(index: number): string {
  return `${managementModalId(index)}-terminate-confirm-input`;
}

function managementTerminationSubmitId(index: number): string {
  return `${managementModalId(index)}-terminate-submit`;
}

function managementActionUrl(template: string, membershipTypeCode: string): string {
  return fillUrlTemplate(
    fillUrlTemplate(template, "__username__", props.membership.username),
    "__membership_type_code__",
    membershipTypeCode,
  );
}

function managementMinValue(): string {
  return new Date().toISOString().slice(0, 10);
}

const terminationConfirmByKey = ref<Record<string, string>>({});

function normalized(value: string): string {
  return value.toLowerCase().trim();
}

function terminationMatches(entry: UserProfileMembershipEntry): boolean {
  if (!entry.canManage) {
    return false;
  }
  return normalized(terminationConfirmByKey.value[entry.key] || "") === normalized(props.membership.username);
}

function resetTermination(entry: UserProfileMembershipEntry): void {
  terminationConfirmByKey.value[entry.key] = "";
}
</script>

<template>
  <MembershipCard
    v-if="membership.showCard"
    data-user-profile-membership-root
    :notes="notes"
    :request-detail-template="membershipRequestDetailUrlTemplate"
  >
    <template #actions>
        <a v-if="membership.canViewHistory" :href="historyUrl(membership.username)" class="btn btn-sm btn-outline-secondary" title="View membership history">History</a>
        <a v-if="membership.isOwner && membership.canRequestAny" :href="props.membershipRequestUrl" class="btn btn-sm btn-outline-primary" title="Request membership">Request membership</a>
    </template>

    <li v-for="(entry, index) in membership.entries" :key="entry.key" class="list-group-item d-flex justify-content-between align-items-center">
      <div>
        <div class="font-weight-bold">{{ entry.membershipType.name }}</div>
        <div v-if="entry.membershipType.description" class="text-muted small">{{ entry.membershipType.description }}</div>
        <div v-if="memberSinceLabel(entry)" class="text-muted small">Member since {{ memberSinceLabel(entry) }}</div>
        <div v-if="expiresLabel(entry)" class="small" :class="expiresToneClass(entry)">
          <i v-if="entry.isExpiringSoon" class="fas fa-exclamation-triangle mr-1" />
          Expires {{ expiresLabel(entry) }}
        </div>
      </div>
      <div class="d-flex align-items-center justify-content-end flex-wrap" style="gap: .5rem;">
        <component :is="badgeTag(entry.requestId)" :href="requestDetailUrl(entry.requestId) || undefined" :class="membershipBadgeClass(entry)">{{ entry.membershipType.name }}</component>
        <a v-if="entry.canRenew" :href="membershipRequestActionUrl(entry.membershipType.code)" class="btn btn-sm btn-primary" title="Request renewal for this membership">Request renewal</a>
        <a v-if="entry.canRequestTierChange" :href="membershipRequestActionUrl(entry.membershipType.code)" class="btn btn-sm btn-outline-primary" title="Request a change of tier">Change tier</a>
        <button
          v-if="entry.canManage"
          type="button"
          class="btn btn-sm btn-outline-secondary"
          data-toggle="modal"
          :data-target="`#${managementModalId(index + 1)}`"
          title="Edit membership expiration date"
        >Edit expiration</button>
      </div>

      <div
        v-if="entry.canManage"
        class="modal fade"
        :id="managementModalId(index + 1)"
        tabindex="-1"
        role="dialog"
        :aria-labelledby="`${managementModalId(index + 1)}-label`"
        aria-hidden="true"
      >
        <div class="modal-dialog" role="document">
          <div class="modal-content">
            <div class="modal-header">
              <div class="mr-3">
                <h5 class="modal-title mb-0" :id="`${managementModalId(index + 1)}-label`">Manage membership: {{ entry.membershipType.name }} for {{ membership.username }}</h5>
              </div>
              <button type="button" class="close" data-dismiss="modal" aria-label="Close" title="Close dialog">
                <span aria-hidden="true">&times;</span>
              </button>
            </div>

            <div class="modal-body">
              <div v-if="expiresLabel(entry)" class="mb-3">Current expiration: {{ expiresLabel(entry) }}</div>

              <form method="post" :action="managementActionUrl(membershipManagement.expiryUrlTemplate, entry.membershipType.code)" novalidate>
                <input type="hidden" name="csrfmiddlewaretoken" :value="membershipManagement.csrfToken">
                <input v-if="membershipManagement.nextUrl" type="hidden" name="next" :value="membershipManagement.nextUrl">

                <div class="form-group">
                  <label :for="managementInputId(index + 1)">Expiration date</label>
                  <input
                    :id="managementInputId(index + 1)"
                    name="expires_on"
                    type="date"
                    class="form-control"
                    :value="formatDateInputValue(entry.expiresAt)"
                    :min="managementMinValue()"
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
                      :data-target="`#${managementCollapseId(index + 1)}`"
                      aria-expanded="false"
                      :aria-controls="managementCollapseId(index + 1)"
                      title="Show termination confirmation"
                    >Terminate membership&hellip;</button>
                  </div>

                  <div class="collapse mt-3" :id="managementCollapseId(index + 1)">
                    <div class="alert alert-danger mb-3" role="alert">This will end the membership early and cannot be undone.</div>

                    <form method="post" :action="managementActionUrl(membershipManagement.terminateUrlTemplate, entry.membershipType.code)" class="m-0" novalidate>
                      <input type="hidden" name="csrfmiddlewaretoken" :value="membershipManagement.csrfToken">
                      <input v-if="membershipManagement.nextUrl" type="hidden" name="next" :value="membershipManagement.nextUrl">

                      <div class="form-group">
                        <label :for="managementTerminationInputId(index + 1)">Type the name to confirm</label>
                        <input
                          :id="managementTerminationInputId(index + 1)"
                          v-model="terminationConfirmByKey[entry.key]"
                          name="confirm"
                          type="text"
                          class="form-control"
                          :class="{
                            'is-valid': Boolean(terminationConfirmByKey[entry.key]) && terminationMatches(entry),
                            'is-invalid': Boolean(terminationConfirmByKey[entry.key]) && !terminationMatches(entry),
                          }"
                          :placeholder="membership.username"
                          autocomplete="off"
                          :data-terminate-target="membership.username"
                          required
                        >
                        <div class="invalid-feedback">Does not match. Type the name to enable termination (case-insensitive).</div>
                      </div>

                      <div class="d-flex justify-content-end">
                        <button type="button" class="btn btn-outline-secondary" data-terminate-action="cancel" title="Cancel termination and collapse this section" @click="resetTermination(entry)">Cancel termination</button>
                        <button
                          type="submit"
                          class="btn btn-danger ml-2"
                          :id="managementTerminationSubmitId(index + 1)"
                          :disabled="!terminationMatches(entry)"
                          :aria-disabled="terminationMatches(entry) ? 'false' : 'true'"
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

    <li v-if="!membership.entries.length && !membership.pendingEntries.length" class="list-group-item text-muted">
      No memberships yet.
      <a v-if="membership.isOwner && membership.canRequestAny" :href="props.membershipRequestUrl">Request membership</a>
    </li>

    <li v-for="entry in membership.pendingEntries" :key="entry.key" class="list-group-item d-flex justify-content-between align-items-center">
      <div>
        <div class="font-weight-bold">{{ entry.membershipType.name }}</div>
        <div class="small text-muted">
          <a :href="requestDetailUrl(entry.requestId)">Request #{{ entry.requestId }}</a>
        </div>
        <div v-if="entry.organizationName" class="small text-muted">Organization: {{ entry.organizationName }}</div>
        <div v-if="entry.membershipType.description" class="small text-muted">{{ entry.membershipType.description }}</div>
      </div>
      <component :is="badgeTag(entry.requestId)" :href="requestDetailUrl(entry.requestId) || undefined" :class="pendingBadge(entry.status).className">{{ pendingBadge(entry.status).label }}</component>
    </li>
  </MembershipCard>
</template>