<script setup lang="ts">
import MembershipNotesCard from "../../membership-requests/components/MembershipNotesCard.vue";

interface MembershipCardNotes {
  summaryUrl: string;
  detailUrl: string;
  addUrl: string;
  csrfToken: string;
  nextUrl: string;
  canView: boolean;
  canWrite: boolean;
  targetType: string;
  target: string;
}

withDefaults(
  defineProps<{
    title?: string;
    notes?: MembershipCardNotes | null;
    requestDetailTemplate?: string;
  }>(),
  {
    title: "Membership",
    notes: null,
    requestDetailTemplate: undefined,
  },
);
</script>

<template>
  <ul class="list-group mb-3" data-membership-card-root>
    <li class="list-group-item d-flex justify-content-between align-items-center">
      <strong>{{ title }}</strong>
      <div v-if="$slots.actions" class="d-flex align-items-center flex-wrap" style="gap: .5rem;">
        <slot name="actions" />
      </div>
    </li>

    <slot />

    <li v-if="notes" class="list-group-item p-0">
      <MembershipNotesCard
        :request-id="0"
        :summary-url="notes.summaryUrl"
        :detail-url="notes.detailUrl"
        :add-url="notes.addUrl"
        :request-detail-template="requestDetailTemplate"
        :csrf-token="notes.csrfToken"
        :next-url="notes.nextUrl"
        :can-view="notes.canView"
        :can-write="notes.canWrite"
        :can-vote="false"
        :target-type="notes.targetType"
        :target="notes.target"
        :initial-open="false"
      />
    </li>
  </ul>
</template>