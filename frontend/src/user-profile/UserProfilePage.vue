<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { formatFixedCurrentTime } from "../shared/membershipPresentation";
import UserProfileGroupsPanel from "./UserProfileGroupsPanel.vue";
import UserProfileMembershipPanel from "./UserProfileMembershipPanel.vue";
import UserProfileSummary from "./UserProfileSummary.vue";
import { fillUrlTemplate } from "../shared/urlTemplates";
import type { UserProfileActionItem, UserProfileBootstrap, UserProfileResponse, UserProfileSummaryBootstrap } from "./types";

const props = defineProps<{
  bootstrap: UserProfileBootstrap;
}>();

const payload = ref<UserProfileResponse | null>(null);
const error = ref("");
const isLoading = ref(false);
const currentTimeLabel = ref("");
const recommendedDismissed = ref(false);
let clockIntervalId: number | null = null;

const summary = computed<UserProfileSummaryBootstrap | null>(() => {
  if (!payload.value) {
    return null;
  }
  return payload.value.summary;
});

const ACTION_COPY: Record<string, { label: string; urlLabel: string }> = {
  "coc-not-signed-alert": { label: "Sign the Code of Conduct", urlLabel: "Review agreement" },
  "country-code-missing-alert": { label: "Add your country", urlLabel: "Set country code" },
  "email-blacklisted-alert": { label: "Fix your email address", urlLabel: "Review emails" },
  "membership-action-required-alert": { label: "Respond to the membership request", urlLabel: "Open request" },
  "sponsorship-action-required-alert": { label: "Respond to the sponsorship request", urlLabel: "Open request" },
  "membership-request-recommended-alert": { label: "Request membership", urlLabel: "Request" },
};

function updateCurrentTime(): void {
  const timezoneName = payload.value?.summary.timezoneName || "";
  if (!timezoneName) {
    return;
  }

  try {
    currentTimeLabel.value = formatFixedCurrentTime(new Date(), timezoneName);
  } catch {
    currentTimeLabel.value = "";
  }
}

function startClock(): void {
  if (clockIntervalId !== null) {
    window.clearInterval(clockIntervalId);
  }
  updateCurrentTime();
  if (payload.value?.summary.timezoneName) {
    clockIntervalId = window.setInterval(updateCurrentTime, 1000);
  }
}

function loadRecommendedDismissalState(): void {
  const dismissKey = payload.value?.accountSetup.recommendedDismissKey || "";
  if (!dismissKey) {
    recommendedDismissed.value = false;
    return;
  }

  try {
    recommendedDismissed.value = window.localStorage?.getItem(dismissKey) === "1";
  } catch {
    recommendedDismissed.value = false;
  }
}

function dismissRecommended(): void {
  const dismissKey = payload.value?.accountSetup.recommendedDismissKey || "";
  recommendedDismissed.value = true;
  if (!dismissKey) {
    return;
  }

  try {
    window.localStorage?.setItem(dismissKey, "1");
  } catch {
    // Storage can be unavailable in private browsing; the visible dismissal still works for this render.
  }
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
      error.value = "Unable to load profile right now.";
      return;
    }
    payload.value = (await response.json()) as UserProfileResponse;
    startClock();
    loadRecommendedDismissalState();
  } catch {
    error.value = "Unable to load profile right now.";
  } finally {
    isLoading.value = false;
  }
}

function actionKey(action: UserProfileActionItem): string {
  return action.id;
}

function actionLabel(action: UserProfileActionItem): string {
  return ACTION_COPY[action.id]?.label || action.id;
}

function actionUrlLabel(action: UserProfileActionItem): string {
  return ACTION_COPY[action.id]?.urlLabel || "Open";
}

function actionHref(action: UserProfileActionItem): string {
  switch (action.id) {
    case "coc-not-signed-alert":
      return action.agreementCn
        ? fillUrlTemplate(props.bootstrap.agreementsUrlTemplate, "__agreement_cn__", action.agreementCn)
        : "";
    case "country-code-missing-alert":
      return props.bootstrap.settingsCountryCodeUrl;
    case "email-blacklisted-alert":
      return props.bootstrap.settingsEmailsUrl;
    case "membership-action-required-alert":
    case "sponsorship-action-required-alert":
      return typeof action.requestId === "number"
        ? fillUrlTemplate(props.bootstrap.membershipRequestDetailUrlTemplate, "__request_id__", action.requestId)
        : "";
    case "membership-request-recommended-alert":
      return props.bootstrap.membershipRequestUrl;
    default:
      return "";
  }
}

onMounted(async () => {
  await load();
});

onBeforeUnmount(() => {
  if (clockIntervalId !== null) {
    window.clearInterval(clockIntervalId);
  }
});
</script>

<template>
  <div data-user-profile-page-vue-root>
    <div v-if="error" class="text-muted mb-3">{{ error }}</div>
    <div v-else-if="isLoading && !payload" class="text-muted mb-3">Loading profile...</div>
    <template v-else-if="payload && summary">
      <div
        v-if="payload.accountSetup.requiredActions.length"
        id="account-setup-required-alert"
        class="alert mb-3"
        :class="payload.accountSetup.requiredIsRfi ? 'alert-warning' : 'alert-danger'"
        role="alert"
      >
        <div class="font-weight-bold mb-1">Action required</div>
        <ul class="mb-0 pl-3">
          <li v-for="action in payload.accountSetup.requiredActions" :key="actionKey(action)">
            {{ actionLabel(action) }}
            <a :href="actionHref(action)">{{ actionUrlLabel(action) }}</a>
          </li>
        </ul>
      </div>

      <div
        v-if="payload.accountSetup.recommendedActions.length && !recommendedDismissed"
        id="account-setup-recommended-alert"
        class="alert alert-info alert-dismissible fade show mb-3"
        :data-dismiss-key="payload.accountSetup.recommendedDismissKey"
        role="alert"
      >
        <button type="button" class="close" aria-label="Close" data-dismiss="alert" @click="dismissRecommended">
          <span aria-hidden="true">&times;</span>
        </button>
        <div class="font-weight-bold mb-1">Recommended</div>
        <ul class="mb-0 pl-3">
          <li v-for="action in payload.accountSetup.recommendedActions" :key="actionKey(action)">
            {{ actionLabel(action) }}
            <a :href="actionHref(action)">{{ actionUrlLabel(action) }}</a>
          </li>
        </ul>
      </div>

      <div class="row">
        <div class="col-md-4">
          <UserProfileSummary :bootstrap="summary" :current-time-label="currentTimeLabel" :settings-profile-url="bootstrap.settingsProfileUrl" />
        </div>
        <div class="col-md-8">
          <UserProfileMembershipPanel
            :membership="payload.membership"
            :timezone-name="summary.timezoneName"
            :membership-history-url-template="bootstrap.membershipHistoryUrlTemplate"
            :membership-request-url="bootstrap.membershipRequestUrl"
            :membership-request-detail-url-template="bootstrap.membershipRequestDetailUrlTemplate"
            :membership-management="bootstrap.membershipManagement"
            :membership-notes="bootstrap.membershipNotes"
          />
          <UserProfileGroupsPanel
            :bootstrap="payload.groups"
            :group-detail-url-template="bootstrap.groupDetailUrlTemplate"
            :agreements-url-template="bootstrap.agreementsUrlTemplate"
          />
        </div>
      </div>
    </template>
  </div>
</template>