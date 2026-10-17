<script setup lang="ts">
import { onMounted, ref } from "vue";

import ServerFormFields from "../shared/components/ServerFormFields.vue";
import { fetchRegisterActivatePayload, type RegisterActivateBootstrap, type RegisterActivatePayload } from "./types";

const props = defineProps<{
  bootstrap: RegisterActivateBootstrap;
}>();

const payload = ref<RegisterActivatePayload | null>(props.bootstrap.initialPayload);
const loadError = ref("");

const fieldLabels: Record<string, string> = {
  password: "Password",
  password_confirm: "Confirm password",
};
const fieldHelpTexts: Record<string, string> = {
  password: "Choose a strong password.",
};

async function loadPayload(): Promise<void> {
  if (payload.value !== null || !props.bootstrap.apiUrl) {
    return;
  }
  try {
    payload.value = await fetchRegisterActivatePayload(props.bootstrap.apiUrl);
  } catch {
    loadError.value = "Unable to load activation form right now.";
  }
}

onMounted(async () => {
  await loadPayload();
});
</script>

<template>
  <div data-register-activate-shell>
    <div v-if="loadError" class="alert alert-danger" role="alert">{{ loadError }}</div>
    <div v-else-if="!payload" class="text-muted">Loading activation form...</div>
    <div v-else class="row justify-content-center">
      <div class="col-md-8 col-lg-7">
        <div class="card card-primary">
          <small class="text-muted d-block px-3 pt-2 pb-1">Step 3 of 3: Choose a password</small>

          <form :action="bootstrap.submitUrl" method="post" novalidate>
            <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken || ''">
            <div class="card-body">
              <p>Hello <strong>{{ payload.username }}</strong>. To activate your account, please choose a password.</p>

              <ServerFormFields :form="payload.form" :labels="fieldLabels" :help-texts="fieldHelpTexts" />
            </div>
            <div class="card-footer d-flex justify-content-between align-items-center">
              <button type="submit" class="btn btn-primary" title="Activate account and set password">Activate</button>
              <a :href="bootstrap.startOverUrl" class="btn btn-link" title="Return to registration">Start over</a>
            </div>
          </form>
        </div>
      </div>
    </div>
  </div>
</template>