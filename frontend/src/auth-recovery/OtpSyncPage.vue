<script setup lang="ts">
import { onMounted, ref } from "vue";

import ServerFormFields from "../shared/components/ServerFormFields.vue";
import { fetchOtpSyncPayload, type OtpSyncBootstrap, type OtpSyncPayload } from "./types";

const props = defineProps<{
  bootstrap: OtpSyncBootstrap;
}>();

const payload = ref<OtpSyncPayload | null>(props.bootstrap.initialPayload);
const loadError = ref("");

const fieldLabels: Record<string, string> = {
  username: "Username",
  password: "Password",
  first_code: "First OTP",
  second_code: "Second OTP",
  token: "Token ID",
};
const fieldHelpTexts: Record<string, string> = {
  token: "Optional. Leave empty to sync the default token.",
};

async function loadPayload(): Promise<void> {
  if (payload.value !== null || !props.bootstrap.apiUrl) {
    return;
  }
  try {
    payload.value = await fetchOtpSyncPayload(props.bootstrap.apiUrl);
  } catch {
    loadError.value = "Unable to load OTP sync form right now.";
  }
}

onMounted(async () => {
  await loadPayload();
});
</script>

<template>
  <div data-auth-recovery-otp-sync-shell>
    <div v-if="loadError" class="alert alert-danger" role="alert">{{ loadError }}</div>
    <div v-else-if="!payload" class="text-muted">Loading OTP sync form...</div>
    <div v-else class="row justify-content-center">
      <div class="col-md-8 col-lg-7">
        <div class="card card-primary">
          <form :action="bootstrap.submitUrl" method="post" novalidate>
            <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken || ''">

            <div class="card-body">
              <h2 class="h4 mb-3">Synchronize OTP Token</h2>
              <p class="text-muted">Enter two consecutive OTP codes from your authenticator device.</p>

              <ServerFormFields :form="payload.form" :labels="fieldLabels" :help-texts="fieldHelpTexts" />
            </div>

            <div class="card-footer d-flex justify-content-between align-items-center">
              <a :href="bootstrap.loginUrl" class="btn btn-link" title="Return to login">Back to login</a>
              <button type="submit" class="btn btn-primary" title="Synchronize token">Synchronize token</button>
            </div>
          </form>
        </div>
      </div>
    </div>
  </div>
</template>