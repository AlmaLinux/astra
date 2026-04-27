<script setup lang="ts">
import { onMounted, ref } from "vue";

import { fetchRegisterConfirmPayload, type RegisterConfirmBootstrap, type RegisterConfirmPayload } from "./types";

const props = defineProps<{
  bootstrap: RegisterConfirmBootstrap;
}>();

const payload = ref<RegisterConfirmPayload | null>(props.bootstrap.initialPayload);
const loadError = ref("");

async function loadPayload(): Promise<void> {
  if (payload.value !== null || !props.bootstrap.apiUrl) {
    return;
  }
  try {
    payload.value = await fetchRegisterConfirmPayload(props.bootstrap.apiUrl);
  } catch {
    loadError.value = "Unable to load email validation details right now.";
  }
}

onMounted(async () => {
  await loadPayload();
});
</script>

<template>
  <div data-register-confirm-shell>
    <div v-if="loadError" class="alert alert-danger" role="alert">{{ loadError }}</div>
    <div v-else-if="!payload" class="text-muted">Loading email validation details...</div>
    <div v-else class="row justify-content-center">
      <div class="col-md-8 col-lg-7">
        <div class="card card-primary">
          <div class="card-body">
            <small class="text-muted d-block px-3 pt-2 pb-1">Step 2 of 3: Verify your email</small>
            <p>We created the account for <strong>{{ payload.username }}</strong>.</p>
            <p>
              Before you can log in, your email address
              <template v-if="payload.email !== null">: <strong>{{ payload.email }}</strong></template>
              needs to be validated. Please check your inbox and click on the link to proceed.
            </p>
            <p class="text-muted">If you can't find the email in a couple minutes, check your spam folder. If it's not there, you can ask for another email below.</p>

            <form :action="bootstrap.submitUrl" method="post" class="mt-3">
              <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken || ''">
              <input
                v-for="field in payload.form.fields"
                :key="field.name"
                type="hidden"
                :name="field.name"
                :id="field.id"
                :value="field.value"
              >
              <button type="submit" class="btn btn-secondary" title="Resend verification email">Resend email</button>
            </form>
          </div>
          <div class="card-footer d-flex justify-content-between">
            <a :href="bootstrap.loginUrl" class="btn btn-link" title="Return to login">Back to login</a>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>