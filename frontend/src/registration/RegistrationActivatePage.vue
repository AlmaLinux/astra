<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { fetchRegisterActivatePayload, type RegisterActivateBootstrap, type RegisterActivatePayload, type RegistrationFormField } from "./types";

const props = defineProps<{
  bootstrap: RegisterActivateBootstrap;
}>();

const payload = ref<RegisterActivatePayload | null>(props.bootstrap.initialPayload);
const loadError = ref("");

const visibleFields = computed(() => payload.value?.form.fields.filter((field) => field.widget !== "hidden") ?? []);

const fieldLabels: Record<string, string> = {
  password: "Password",
  password_confirm: "Confirm password",
};

function fieldLabel(field: RegistrationFormField): string {
  return fieldLabels[field.name] || field.name;
}

function fieldHelpText(field: RegistrationFormField): string {
  if (field.name === "password") {
    return "Choose a strong password.";
  }
  return "";
}

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
              <button type="submit" class="btn btn-primary" title="Activate account and set password">Activate</button>
              <a :href="bootstrap.startOverUrl" class="btn btn-link" title="Return to registration">Start over</a>
            </div>
          </form>
        </div>
      </div>
    </div>
  </div>
</template>