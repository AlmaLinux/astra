<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import {
  fetchPasswordResetRequestPayload,
  type AuthRecoveryFormField,
  type PasswordResetRequestBootstrap,
  type PasswordResetRequestPayload,
} from "./types";

const props = defineProps<{
  bootstrap: PasswordResetRequestBootstrap;
}>();

const payload = ref<PasswordResetRequestPayload | null>(props.bootstrap.initialPayload);
const loadError = ref("");

const hiddenFields = computed(() => payload.value?.form.fields.filter((field) => field.widget === "hidden") ?? []);
const visibleFields = computed(() => payload.value?.form.fields.filter((field) => field.widget !== "hidden") ?? []);

const fieldLabels: Record<string, string> = {
  username_or_email: "Username or email",
};

function fieldLabel(field: AuthRecoveryFormField): string {
  return fieldLabels[field.name] || field.name;
}

async function loadPayload(): Promise<void> {
  if (payload.value !== null || !props.bootstrap.apiUrl) {
    return;
  }
  try {
    payload.value = await fetchPasswordResetRequestPayload(props.bootstrap.apiUrl);
  } catch {
    loadError.value = "Unable to load password reset form right now.";
  }
}

onMounted(async () => {
  await loadPayload();
});
</script>

<template>
  <div data-auth-recovery-password-reset-shell>
    <div v-if="loadError" class="alert alert-danger" role="alert">{{ loadError }}</div>
    <div v-else-if="!payload" class="text-muted">Loading password reset form...</div>
    <div v-else class="row justify-content-center">
      <div class="col-md-8 col-lg-6">
        <div class="card card-primary">
          <form :action="bootstrap.submitUrl" method="post" novalidate>
            <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken || ''">
            <input
              v-for="field in hiddenFields"
              :key="field.name"
              type="hidden"
              :name="field.name"
              :id="field.id"
              :value="field.value"
            >

            <div class="card-body">
              <h2 class="h4 mb-3">Reset password</h2>
              <p class="text-muted">Enter the username or email address for your account. We will send you a password reset link if the account exists.</p>

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
                <div v-for="fieldError in field.errors" :key="fieldError" class="invalid-feedback d-block">{{ fieldError }}</div>
              </div>
            </div>

            <div class="card-footer d-flex justify-content-between align-items-center">
              <a :href="bootstrap.loginUrl" class="btn btn-link" title="Return to login">Back to login</a>
              <button type="submit" class="btn btn-primary" title="Send password reset email">Send reset email</button>
            </div>
          </form>
        </div>
      </div>
    </div>
  </div>
</template>