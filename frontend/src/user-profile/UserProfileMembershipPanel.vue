<script setup lang="ts">
import { computed } from "vue";

import MembershipCard from "../shared/components/MembershipCard.vue";
import MembershipManageModal from "../shared/components/MembershipManageModal.vue";
import { formatFullDate, formatPreciseDateTime, membershipTierClass, membershipTypeDescription, pendingMembershipBadge } from "../shared/membershipPresentation";
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
  return formatFullDate(entry.createdAt);
}

function expiresLabel(entry: UserProfileMembershipEntry): string {
  if (entry.isExpiringSoon) {
    return formatPreciseDateTime(entry.expiresAt, props.timezoneName);
  }
  return formatFullDate(entry.expiresAt);
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

function managementActionUrl(template: string, entry: UserProfileMembershipEntry): string {
  return fillUrlTemplate(template, "__membership_id__", entry.id);
}

function managementMinValue(): string {
  return new Date().toISOString().slice(0, 10);
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
        <div v-if="membershipTypeDescription(entry.membershipType)" class="text-muted small">{{ membershipTypeDescription(entry.membershipType) }}</div>
        <div v-if="memberSinceLabel(entry)" class="text-muted small">Member since {{ memberSinceLabel(entry) }}</div>
        <div v-if="expiresLabel(entry)" class="small" :class="expiresToneClass(entry)">
          <i v-if="entry.isExpiringSoon" class="fas fa-exclamation-triangle mr-1" />
          Expires {{ expiresLabel(entry) }}
        </div>
      </div>
      <div class="d-flex align-items-center justify-content-end flex-wrap" style="gap: .5rem;">
        <component :is="badgeTag(entry.requestId)" :href="requestDetailUrl(entry.requestId) || undefined" :class="membershipBadgeClass(entry)">{{ entry.membershipType.name }}</component>
        <a v-if="entry.canRenew" :href="membershipRequestActionUrl(entry.renewalMembershipTypeCode)" class="btn btn-sm btn-primary" title="Request renewal for this membership">Request renewal</a>
        <a v-if="entry.canRequestTierChange" :href="membershipRequestActionUrl(entry.tierChangeMembershipTypeCode)" class="btn btn-sm btn-outline-primary" title="Request a change of tier">Change tier</a>
        <button
          v-if="entry.canManage"
          type="button"
          class="btn btn-sm btn-outline-secondary"
          data-toggle="modal"
          :data-target="`#${managementModalId(index + 1)}`"
          title="Edit or terminate this membership"
        >Edit membership</button>
      </div>

      <MembershipManageModal
        v-if="entry.canManage"
        :modal-id="managementModalId(index + 1)"
        :membership-type="entry.membershipType"
        :target-name="membership.username"
        target-noun="username"
        :expires-at="entry.expiresAt"
        :expires-label="expiresLabel(entry)"
        :expiry-min-date="managementMinValue()"
        :update-url="managementActionUrl(membershipManagement.updateUrlTemplate, entry)"
        :terminate-url="managementActionUrl(membershipManagement.terminateUrlTemplate, entry)"
        :csrf-token="membershipManagement.csrfToken"
        :next-url="membershipManagement.nextUrl"
      />
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
        <div v-if="membershipTypeDescription(entry.membershipType)" class="small text-muted">{{ membershipTypeDescription(entry.membershipType) }}</div>
      </div>
      <component :is="badgeTag(entry.requestId)" :href="requestDetailUrl(entry.requestId) || undefined" :class="pendingBadge(entry.status).className">{{ pendingBadge(entry.status).label }}</component>
    </li>
  </MembershipCard>
</template>