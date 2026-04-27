<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import {
  fetchSettingsEmailValidationPayload,
  type SettingsEmailValidationBootstrap,
  type SettingsEmailValidationPayload,
} from "./types";

const props = defineProps<{
  bootstrap: SettingsEmailValidationBootstrap;
}>();

const payload = ref<SettingsEmailValidationPayload | null>(props.bootstrap.initialPayload);
const loadError = ref("");

const tabLabels: Record<string, string> = {
  profile: "Profile",
  emails: "Emails",
  keys: "SSH & GPG Keys",
  security: "Security",
  privacy: "Privacy",
  agreements: "Agreements",
  membership: "Membership",
};

const attributeLabel = computed(() => {
  if (payload.value?.emailType === "bugzilla") {
    return "Red Hat Bugzilla Email";
  }
  return "E-mail Address";
});

function tabHref(tab: string): string {
  const routeConfig = props.bootstrap.routeConfig;
  switch (tab) {
    case "profile":
      return routeConfig.profileUrl;
    case "emails":
      return routeConfig.emailsUrl;
    case "keys":
      return routeConfig.keysUrl;
    case "security":
      return routeConfig.securityUrl;
    case "privacy":
      return routeConfig.privacyUrl;
    case "membership":
      return routeConfig.membershipUrl;
    case "agreements":
      return routeConfig.agreementsUrl;
    default:
      return routeConfig.emailsUrl;
  }
}

async function loadPayload(): Promise<void> {
  if (payload.value !== null || !props.bootstrap.apiUrl) {
    return;
  }

  try {
    payload.value = await fetchSettingsEmailValidationPayload(props.bootstrap.apiUrl);
  } catch {
    loadError.value = "Unable to load email validation right now.";
  }
}

onMounted(async () => {
  await loadPayload();
});
</script>

<template>
  <div data-settings-email-validation-page>
    <div v-if="loadError" class="alert alert-danger" role="alert">{{ loadError }}</div>
    <div v-else-if="!payload" class="text-muted">Loading email validation...</div>
    <div v-else class="settings-page">
      <h1 class="m-0 mb-3">
        Settings for
        <a :href="bootstrap.routeConfig.userProfileUrl">{{ bootstrap.username }}</a>
      </h1>

      <div class="card card-primary card-tabs settings-card mt-3">
        <div class="card-header p-0 pt-1">
          <ul class="nav nav-tabs" role="tablist">
            <li v-for="tab in bootstrap.visibleTabs" :key="tab" class="nav-item">
              <a
                class="nav-link"
                :class="{ active: tab === 'emails' }"
                :href="tabHref(tab)"
                :data-settings-tab="tab"
                role="tab"
              >{{ tabLabels[tab] || tab }}</a>
            </li>
          </ul>
        </div>

        <div class="card-body">
          <h3 class="card-title mb-3">Confirm email address change</h3>
          <p class="mb-2">
            You requested to update your <strong>{{ attributeLabel }}</strong> to <strong>{{ payload.email }}</strong>.
          </p>
          <p class="text-muted mb-0">
            Click <strong>Confirm</strong> to save the new address. A brief verification may take effect immediately.
            If you did not request this change, you can cancel below.
          </p>
        </div>

        <div class="card-footer">
          <form :action="bootstrap.submitUrl" method="post" class="m-0">
            <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
            <div class="d-flex justify-content-between">
              <a class="btn btn-secondary" :href="bootstrap.cancelUrl" title="Cancel email change">Cancel</a>
              <button class="btn btn-primary" type="submit" title="Confirm email change">Confirm</button>
            </div>
          </form>
        </div>
      </div>
    </div>
  </div>
</template>