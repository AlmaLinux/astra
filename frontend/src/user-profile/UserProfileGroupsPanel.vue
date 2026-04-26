<script setup lang="ts">
import { fillUrlTemplate } from "../shared/urlTemplates";

import type { UserProfileGroupsBootstrap } from "./types";

const props = defineProps<{
  bootstrap: UserProfileGroupsBootstrap;
  groupDetailUrlTemplate: string;
  agreementsUrlTemplate: string;
}>();

function groupDetailUrl(groupName: string): string {
  return fillUrlTemplate(props.groupDetailUrlTemplate, "__group_name__", groupName);
}

function agreementSettingsUrl(agreementCn: string): string {
  return fillUrlTemplate(props.agreementsUrlTemplate, "__agreement_cn__", agreementCn);
}
</script>

<template>
  <ul class="list-group profile-memberships" data-user-profile-groups-root-vue>
    <li class="list-group-item profile-memberships-header text-right">
      <strong>
        {{ bootstrap.groups.length }} Group{{ bootstrap.groups.length === 1 ? '' : 's' }}
        <template v-if="bootstrap.agreements.length">, {{ bootstrap.agreements.length }} Signed Agreement{{ bootstrap.agreements.length === 1 ? '' : 's' }}</template>
        <template v-if="bootstrap.missingAgreements.length">, {{ bootstrap.missingAgreements.length }} Missing Agreement{{ bootstrap.missingAgreements.length === 1 ? '' : 's' }}</template>
      </strong>
    </li>

    <template v-if="bootstrap.groups.length || bootstrap.agreements.length || bootstrap.missingAgreements.length">
      <li
        v-for="agreement in bootstrap.missingAgreements"
        :key="`missing-${agreement.cn}`"
        class="list-group-item d-flex justify-content-between align-items-center"
      >
        <div class="d-flex align-items-center">
          <i class="fas fa-exclamation-triangle profile-group-icon" />
          <div class="profile-group-name">
            <a v-if="bootstrap.isSelf" :href="agreementSettingsUrl(agreement.cn)">{{ agreement.cn }}</a>
            <template v-else>{{ agreement.cn }}</template>
            <div v-if="agreement.requiredBy.length" class="small text-muted">Required for: {{ agreement.requiredBy.join(', ') }}</div>
          </div>
        </div>
        <span class="badge badge-danger">Required</span>
      </li>

      <li
        v-for="agreement in bootstrap.agreements"
        :key="`signed-${agreement}`"
        class="list-group-item d-flex justify-content-between align-items-center"
      >
        <div class="d-flex align-items-center">
          <i class="fa fa-check-circle profile-group-icon" />
          <div class="profile-group-name">{{ agreement }}</div>
        </div>
        <span class="badge badge-success">Signed</span>
      </li>

      <li
        v-for="group in bootstrap.groups"
        :key="`group-${group.cn}`"
        class="list-group-item d-flex justify-content-between align-items-center"
      >
        <div class="d-flex align-items-center">
          <i class="fas fa-users profile-group-icon" />
          <div class="profile-group-name">
            <a :href="groupDetailUrl(group.cn)">{{ group.cn }}</a>
          </div>
        </div>
        <span v-if="group.role === 'Sponsor'" class="badge badge-primary">Team Lead</span>
        <span v-else class="badge badge-secondary">Member</span>
      </li>
    </template>

    <li v-else class="list-group-item text-muted text-center py-5">
      {{ bootstrap.username }} has no group memberships
    </li>
  </ul>
</template>
