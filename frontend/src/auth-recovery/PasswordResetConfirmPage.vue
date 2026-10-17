<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import ServerFormFields from "../shared/components/ServerFormFields.vue";
import { fetchPasswordResetConfirmPayload, type PasswordResetConfirmBootstrap, type PasswordResetConfirmPayload } from "./types";

const props = defineProps<{
  bootstrap: PasswordResetConfirmBootstrap;
}>();

const payload = ref<PasswordResetConfirmPayload | null>(props.bootstrap.initialPayload);
const loadError = ref("");

const fieldLabels: Record<string, string> = {
  password: "New password",
  password_confirm: "Confirm new password",
  otp: "OTP code",
};
const fieldHelpTexts = computed<Record<string, string>>(() => ({
  password: "Choose a strong password.",
  otp: payload.value?.hasOtp
    ? "Required because two-factor authentication is enabled for this account."
    : "Only required if your account has two-factor authentication enabled.",
}));

async function loadPayload(): Promise<void> {
  if (payload.value !== null || !props.bootstrap.apiUrl) {
    return;
  }
  try {
    payload.value = await fetchPasswordResetConfirmPayload(props.bootstrap.apiUrl);
  } catch {
    loadError.value = "Unable to load password reset confirmation form right now.";
  }
}

onMounted(async () => {
  await loadPayload();
});
</script>

<template>
  <div data-auth-recovery-password-reset-confirm-shell>
    <div v-if="loadError" class="alert alert-danger" role="alert">{{ loadError }}</div>
    <div v-else-if="!payload" class="text-muted">Loading password reset confirmation form...</div>
    <div v-else class="row justify-content-center">
      <div class="col-md-8 col-lg-7">
        <div class="card card-primary">
          <form :action="bootstrap.submitUrl" method="post" novalidate>
            <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken || ''">
            <input type="hidden" name="token" :value="bootstrap.token">

            <div class="card-body">
              <h2 class="h4 mb-3">Set a new password</h2>
              <p>Choose a new password for <strong>{{ payload.username }}</strong>.</p>

              <ServerFormFields :form="payload.form" :labels="fieldLabels" :help-texts="fieldHelpTexts" />
            </div>

            <div class="card-footer d-flex justify-content-between align-items-center">
              <a :href="bootstrap.loginUrl" class="btn btn-link" title="Return to login">Back to login</a>
              <button type="submit" class="btn btn-primary" title="Update password">Update password</button>
            </div>
          </form>
        </div>
      </div>
    </div>
  </div>
</template>