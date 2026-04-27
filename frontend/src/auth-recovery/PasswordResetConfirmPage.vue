<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import {
  fetchPasswordResetConfirmPayload,
  type AuthRecoveryFormField,
  type PasswordResetConfirmBootstrap,
  type PasswordResetConfirmPayload,
} from "./types";

const props = defineProps<{
  bootstrap: PasswordResetConfirmBootstrap;
}>();

const payload = ref<PasswordResetConfirmPayload | null>(props.bootstrap.initialPayload);
const loadError = ref("");

const visibleFields = computed(() => payload.value?.form.fields.filter((field) => field.widget !== "hidden") ?? []);

const fieldLabels: Record<string, string> = {
  password: "New password",
  password_confirm: "Confirm new password",
  otp: "OTP code",
};

function fieldLabel(field: AuthRecoveryFormField): string {
  return fieldLabels[field.name] || field.name;
}

function fieldHelpText(field: AuthRecoveryFormField): string {
  if (field.name === "password") {
    return "Choose a strong password.";
  }
  if (field.name === "otp") {
    return payload.value?.hasOtp
      ? "Required because two-factor authentication is enabled for this account."
      : "Only required if your account has two-factor authentication enabled.";
  }
  return "";
}

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

              <div v-if="payload.form.nonFieldErrors.length" class="alert alert-danger" role="alert">
                <div v-for="errorItem in payload.form.nonFieldErrors" :key="errorItem">{{ errorItem }}</div>
              </div>

              <div v-for="field in visibleFields" :key="field.name" class="form-group">
                <label :for="field.id">
                  {{ fieldLabel(field) }}
                  <span v-if="field.required" class="form-required-indicator text-danger font-weight-bold ml-1" title="Required" aria-hidden="true">*</span>
                  <span v-if="field.required" class="sr-only">Required</span>
                </label>
                <input
                  :id="field.id"
                  v-model="field.value"
                  :type="field.widget"
                  :name="field.name"
                  class="form-control"
                  :required="field.required"
                  :disabled="field.disabled"
                  v-bind="field.attrs"
                >
                <small v-if="fieldHelpText(field)" class="form-text text-muted">{{ fieldHelpText(field) }}</small>
                <div v-for="fieldError in field.errors" :key="fieldError" class="invalid-feedback d-block">{{ fieldError }}</div>
              </div>
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