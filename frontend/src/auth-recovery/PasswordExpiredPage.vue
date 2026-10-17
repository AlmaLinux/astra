<script setup lang="ts">
import { onMounted, ref } from "vue";

import ServerFormFields from "../shared/components/ServerFormFields.vue";
import { fetchPasswordExpiredPayload, type PasswordExpiredBootstrap, type PasswordExpiredPayload } from "./types";

const props = defineProps<{
  bootstrap: PasswordExpiredBootstrap;
}>();

const payload = ref<PasswordExpiredPayload | null>(props.bootstrap.initialPayload);
const loadError = ref("");

const fieldLabels: Record<string, string> = {
  username: "Username",
  current_password: "Current password",
  otp: "OTP code",
  new_password: "New password",
  confirm_new_password: "Confirm new password",
};

async function loadPayload(): Promise<void> {
  if (payload.value !== null || !props.bootstrap.apiUrl) {
    return;
  }
  try {
    payload.value = await fetchPasswordExpiredPayload(props.bootstrap.apiUrl);
  } catch {
    loadError.value = "Unable to load password expiry form right now.";
  }
}

onMounted(async () => {
  await loadPayload();
});
</script>

<template>
  <div data-auth-recovery-password-expired-shell>
    <div v-if="loadError" class="alert alert-danger" role="alert">{{ loadError }}</div>
    <div v-else-if="!payload" class="text-muted">Loading password expiry form...</div>
    <div v-else class="row justify-content-center">
      <div class="col-md-8 col-lg-7">
        <div class="card card-primary">
          <form :action="bootstrap.submitUrl" method="post" novalidate>
            <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken || ''">

            <div class="card-body">
              <h2 class="h4 mb-3">You must change your password</h2>

              <ServerFormFields :form="payload.form" :labels="fieldLabels" />
            </div>

            <div class="card-footer d-flex justify-content-between align-items-center">
              <a :href="bootstrap.loginUrl" class="btn btn-link" title="Return to login">Back to login</a>
              <button type="submit" class="btn btn-primary" title="Change password">Change password</button>
            </div>
          </form>
        </div>
      </div>
    </div>
  </div>
</template>