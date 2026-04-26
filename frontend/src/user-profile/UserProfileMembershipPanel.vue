<script setup lang="ts">
import { ref } from "vue";

import MembershipCard from "../shared/components/MembershipCard.vue";
import { fillUrlTemplate } from "../shared/urlTemplates";
import type { UserProfileMembershipBadge, UserProfileMembershipEntry, UserProfileMembershipSection } from "./types";

const props = defineProps<{
  membership: UserProfileMembershipSection;
  membershipHistoryUrlTemplate: string;
  membershipRequestUrl: string;
  membershipRequestDetailUrlTemplate: string;
}>();

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

const terminationConfirmByKey = ref<Record<string, string>>({});

function normalized(value: string): string {
  return value.toLowerCase().trim();
}

function terminationMatches(entry: UserProfileMembershipEntry): boolean {
  if (!entry.management) {
    return false;
  }
  return normalized(terminationConfirmByKey.value[entry.key] || "") === normalized(entry.management.terminator);
}

function resetTermination(entry: UserProfileMembershipEntry): void {
  terminationConfirmByKey.value[entry.key] = "";
}
</script>

<template>
  <MembershipCard
    v-if="membership.showCard"
    data-user-profile-membership-root
    :notes="membership.notes"
    :request-detail-template="membershipRequestDetailUrlTemplate"
  >
    <template #actions>
        <a v-if="membership.canViewHistory" :href="historyUrl(membership.username)" class="btn btn-sm btn-outline-secondary" title="View membership history">History</a>
        <a v-if="membership.isOwner && membership.canRequestAny" :href="props.membershipRequestUrl" class="btn btn-sm btn-outline-primary" title="Request membership">Request membership</a>
    </template>

    <li v-for="entry in membership.entries" :key="entry.key" class="list-group-item d-flex justify-content-between align-items-center">
      <div>
        <div class="font-weight-bold">{{ entry.membershipType.name }}</div>
        <div v-if="entry.membershipType.description" class="text-muted small">{{ entry.membershipType.description }}</div>
        <div v-if="entry.memberSinceLabel" class="text-muted small">Member since {{ entry.memberSinceLabel }}</div>
        <div v-if="entry.expiresLabel" class="small" :class="entry.expiresTone === 'danger' ? 'text-danger' : 'text-muted'">
          <i v-if="entry.expiresTone === 'danger'" class="fas fa-exclamation-triangle mr-1" />
          Expires {{ entry.expiresLabel }}
        </div>
      </div>
      <div class="d-flex align-items-center justify-content-end flex-wrap" style="gap: .5rem;">
        <component :is="badgeTag(entry.requestId)" :href="requestDetailUrl(entry.requestId) || undefined" :class="entry.badge.className">{{ entry.badge.label }}</component>
        <a v-if="entry.canRenew" :href="membershipRequestActionUrl(entry.membershipType.code)" class="btn btn-sm btn-primary" title="Request renewal for this membership">Request renewal</a>
        <a v-if="entry.canRequestTierChange" :href="membershipRequestActionUrl(entry.membershipType.code)" class="btn btn-sm btn-outline-primary" title="Request a change of tier">Change tier</a>
        <button
          v-if="entry.management"
          type="button"
          class="btn btn-sm btn-outline-secondary"
          data-toggle="modal"
          :data-target="`#${entry.management.modalId}`"
          title="Edit membership expiration date"
        >Edit expiration</button>
      </div>

      <div
        v-if="entry.management"
        class="modal fade"
        :id="entry.management.modalId"
        tabindex="-1"
        role="dialog"
        :aria-labelledby="`${entry.management.modalId}-label`"
        aria-hidden="true"
      >
        <div class="modal-dialog" role="document">
          <div class="modal-content">
            <div class="modal-header">
              <div class="mr-3">
                <h5 class="modal-title mb-0" :id="`${entry.management.modalId}-label`">Manage membership: {{ entry.membershipType.name }} for {{ entry.management.terminator }}</h5>
              </div>
              <button type="button" class="close" data-dismiss="modal" aria-label="Close" title="Close dialog">
                <span aria-hidden="true">&times;</span>
              </button>
            </div>

            <div class="modal-body">
              <div v-if="entry.management.currentText" class="mb-3">{{ entry.management.currentText }}</div>

              <form method="post" :action="entry.management.expiryActionUrl" novalidate>
                <input type="hidden" name="csrfmiddlewaretoken" :value="entry.management.csrfToken">
                <input v-if="entry.management.nextUrl" type="hidden" name="next" :value="entry.management.nextUrl">

                <div class="form-group">
                  <label :for="entry.management.inputId">Expiration date</label>
                  <input
                    :id="entry.management.inputId"
                    name="expires_on"
                    type="date"
                    class="form-control"
                    :value="entry.management.initialValue"
                    :min="entry.management.minValue"
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
                      :data-target="`#${entry.management.modalId}-terminate-collapse`"
                      aria-expanded="false"
                      :aria-controls="`${entry.management.modalId}-terminate-collapse`"
                      title="Show termination confirmation"
                    >Terminate membership&hellip;</button>
                  </div>

                  <div class="collapse mt-3" :id="`${entry.management.modalId}-terminate-collapse`">
                    <div class="alert alert-danger mb-3" role="alert">This will end the membership early and cannot be undone.</div>

                    <form method="post" :action="entry.management.terminateActionUrl" class="m-0" novalidate>
                      <input type="hidden" name="csrfmiddlewaretoken" :value="entry.management.csrfToken">
                      <input v-if="entry.management.nextUrl" type="hidden" name="next" :value="entry.management.nextUrl">

                      <div class="form-group">
                        <label :for="`${entry.management.modalId}-terminate-confirm-input`">Type the name to confirm</label>
                        <input
                          :id="`${entry.management.modalId}-terminate-confirm-input`"
                          v-model="terminationConfirmByKey[entry.key]"
                          name="confirm"
                          type="text"
                          class="form-control"
                          :class="{
                            'is-valid': Boolean(terminationConfirmByKey[entry.key]) && terminationMatches(entry),
                            'is-invalid': Boolean(terminationConfirmByKey[entry.key]) && !terminationMatches(entry),
                          }"
                          :placeholder="entry.management.terminator"
                          autocomplete="off"
                          :data-terminate-target="entry.management.terminator"
                          required
                        >
                        <div class="invalid-feedback">Does not match. Type the name to enable termination (case-insensitive).</div>
                      </div>

                      <div class="d-flex justify-content-end">
                        <button type="button" class="btn btn-outline-secondary" data-terminate-action="cancel" title="Cancel termination and collapse this section" @click="resetTermination(entry)">Cancel termination</button>
                        <button
                          type="submit"
                          class="btn btn-danger ml-2"
                          :id="`${entry.management.modalId}-terminate-submit`"
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
      <component :is="badgeTag(entry.requestId)" :href="requestDetailUrl(entry.requestId) || undefined" :class="entry.badge.className">{{ entry.badge.label }}</component>
    </li>
  </MembershipCard>
</template>