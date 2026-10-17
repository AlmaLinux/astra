<script setup lang="ts">
import { onMounted, ref } from "vue";

import ServerFormFields from "../shared/components/ServerFormFields.vue";
import { fetchPasswordResetRequestPayload, type PasswordResetRequestBootstrap, type PasswordResetRequestPayload } from "./types";

const props = defineProps<{
  bootstrap: PasswordResetRequestBootstrap;
}>();

const payload = ref<PasswordResetRequestPayload | null>(props.bootstrap.initialPayload);
const loadError = ref("");

const fieldLabels: Record<string, string> = {
  username_or_email: "Username or email",
};

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

            <div class="card-body">
              <h2 class="h4 mb-3">Reset password</h2>
              <p class="text-muted">Enter the username or email address for your account. We will send you a password reset link if the account exists.</p>

              <ServerFormFields :form="payload.form" :labels="fieldLabels" />
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