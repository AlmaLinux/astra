<script setup lang="ts">
export interface WidgetOrganizationMembershipBadge {
  label: string;
  class_name: string;
  request_url: string | null;
}

const props = defineProps<{
  name: string;
  status: string;
  detailUrl: string;
  logoUrl?: string;
  linkToDetail: boolean;
  memberships: WidgetOrganizationMembershipBadge[];
}>();
</script>

<template>
  <div class="card card-body mb-4 px-2 py-3 card-widget widget-user position-relative">
    <div v-if="props.status === 'unclaimed'" class="ribbon-wrapper organization-status-ribbon-widget">
      <div class="ribbon bg-warning">Unclaimed</div>
    </div>
    <div class="d-flex align-items-center">
      <div class="flex-shrink-0 ml-1 mr-3">
        <template v-if="props.linkToDetail">
          <a :href="props.detailUrl">
            <img
              v-if="props.logoUrl"
              :src="props.logoUrl"
              :alt="`${props.name} logo`"
              class="rounded-lg elevation-2"
              style="width: 50px; height: 50px; object-fit: contain; background: #fff;"
            >
            <span
              v-else
              class="rounded-lg elevation-2 d-inline-flex align-items-center justify-content-center bg-secondary"
              style="width: 50px; height: 50px;"
            >
              <i class="fas fa-building" />
            </span>
          </a>
        </template>
        <template v-else>
          <img
            v-if="props.logoUrl"
            :src="props.logoUrl"
            :alt="`${props.name} logo`"
            class="rounded-lg elevation-2"
            style="width: 50px; height: 50px; object-fit: contain; background: #fff;"
          >
          <span
            v-else
            class="rounded-lg elevation-2 d-inline-flex align-items-center justify-content-center bg-secondary"
            style="width: 50px; height: 50px;"
          >
            <i class="fas fa-building" />
          </span>
        </template>
      </div>

      <div class="flex-grow-1 ms-2">
        <div class="my-0 font-weight-bold">
          <a v-if="props.linkToDetail" :href="props.detailUrl">{{ props.name }}</a>
          <template v-else>{{ props.name }}</template>
        </div>
        <div class="mt-1 organization-widget-memberships">
          <template v-for="membership in props.memberships" :key="`${props.name}-${membership.label}`">
            <a
              v-if="membership.request_url"
              :href="membership.request_url"
              :class="`badge alx-status-badge badge-pill p-2 ${membership.class_name} alx-status-badge--active organization-widget-badge`"
            >{{ membership.label }}</a>
            <span
              v-else
              :class="`badge alx-status-badge badge-pill p-2 ${membership.class_name} alx-status-badge--active organization-widget-badge`"
            >{{ membership.label }}</span>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.organization-widget-badge {
  margin-right: 0.375rem;
}
</style>
