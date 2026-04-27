<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { fetchRegisterPagePayload, type RegisterPageBootstrap, type RegisterPagePayload, type RegistrationFormField } from "./types";

const props = defineProps<{
  bootstrap: RegisterPageBootstrap;
}>();

const payload = ref<RegisterPagePayload | null>(props.bootstrap.initialPayload);
const loadError = ref("");

const hiddenFields = computed(() => payload.value?.form.fields.filter((field) => field.widget === "hidden") ?? []);
const visibleFields = computed(() => payload.value?.form.fields.filter((field) => field.widget !== "hidden") ?? []);

const fieldLabels: Record<string, string> = {
  username: "Username",
  first_name: "First name",
  last_name: "Last name",
  email: "Email address",
  over_16: "I am over 16 years old",
};

function fieldLabel(field: RegistrationFormField): string {
  return fieldLabels[field.name] || field.name;
}

function fieldHelpText(field: RegistrationFormField): string {
  if (field.name === "username") {
    return "Lowercase letters (a-z), digits (0-9), and hyphens only. 5-32 characters.";
  }
  return "";
}

function fieldInputClass(field: RegistrationFormField): string {
  return field.widget === "checkbox" ? "form-check-input" : "form-control";
}

async function loadPayload(): Promise<void> {
  if (payload.value !== null || !props.bootstrap.apiUrl) {
    return;
  }
  try {
    payload.value = await fetchRegisterPagePayload(props.bootstrap.apiUrl);
  } catch {
    loadError.value = "Unable to load registration form right now.";
  }
}

onMounted(async () => {
  await loadPayload();
});
</script>

<template>
  <div data-register-shell>
    <div v-if="loadError" class="alert alert-danger" role="alert">{{ loadError }}</div>
    <div v-else-if="!payload" class="text-muted">Loading registration form...</div>
    <div v-else class="row align-items-start public-auth-layout">
      <div class="col-lg-7 d-none d-lg-block">
        <h1 class="public-auth-title">AlmaLinux Accounts</h1>
        <p class="public-auth-lead">
          AlmaLinux Accounts provides the ability to create and manage your account across AlmaLinux's entire infrastructure.
        </p>
      </div>

      <div class="col-12 col-lg-5">
        <div class="card card-primary card-tabs public-auth-card">
          <div class="card-header p-0 pt-1">
            <ul class="nav nav-tabs" role="tablist">
              <li class="nav-item">
                <a class="nav-link" :href="bootstrap.loginUrl">Login</a>
              </li>
              <li class="nav-item">
                <a class="nav-link active" :href="bootstrap.registerUrl">Register</a>
              </li>
            </ul>
          </div>

          <template v-if="!payload.registrationOpen">
            <div class="card-body">
              <div class="alert alert-info">Registration is closed at the moment.</div>
              <a class="btn btn-outline-primary" :href="bootstrap.loginUrl" title="Return to login">Back to login</a>
            </div>
          </template>

          <template v-else>
            <small class="text-muted d-block px-3 pt-2 pb-1">Step 1 of 3: Account details</small>
            <form :action="bootstrap.submitUrl" method="post" class="needs-validation" novalidate>
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
                <div v-if="payload.form.nonFieldErrors.length" class="alert alert-danger" role="alert">
                  <div v-for="errorItem in payload.form.nonFieldErrors" :key="errorItem">{{ errorItem }}</div>
                </div>

                <template v-for="field in visibleFields" :key="field.name">
                  <div v-if="field.widget === 'checkbox'" class="form-group form-check">
                    <input
                      :id="field.id"
                      v-model="field.checked"
                      type="checkbox"
                      :name="field.name"
                      value="on"
                      :class="fieldInputClass(field)"
                      :required="field.required"
                      :disabled="field.disabled"
                      v-bind="field.attrs"
                    >
                    <label class="form-check-label" :for="field.id">
                      {{ fieldLabel(field) }}
                      <span v-if="field.required" class="form-required-indicator text-danger font-weight-bold ml-1" title="Required" aria-hidden="true">*</span>
                      <span v-if="field.required" class="sr-only">Required</span>
                    </label>
                    <div v-for="fieldError in field.errors" :key="fieldError" class="invalid-feedback d-block">{{ fieldError }}</div>
                  </div>

                  <div v-else-if="field.name === 'first_name' || field.name === 'last_name'" class="form-row">
                    <template v-if="field.name === 'first_name'">
                      <div class="form-group col-md-6">
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
                          :class="fieldInputClass(field)"
                          :required="field.required"
                          :disabled="field.disabled"
                          v-bind="field.attrs"
                        >
                        <div v-for="fieldError in field.errors" :key="fieldError" class="invalid-feedback d-block">{{ fieldError }}</div>
                      </div>
                      <div v-for="otherField in visibleFields.filter((candidate) => candidate.name === 'last_name')" :key="otherField.name" class="form-group col-md-6">
                        <label :for="otherField.id">
                          {{ fieldLabel(otherField) }}
                          <span v-if="otherField.required" class="form-required-indicator text-danger font-weight-bold ml-1" title="Required" aria-hidden="true">*</span>
                          <span v-if="otherField.required" class="sr-only">Required</span>
                        </label>
                        <input
                          :id="otherField.id"
                          v-model="otherField.value"
                          :type="otherField.widget"
                          :name="otherField.name"
                          :class="fieldInputClass(otherField)"
                          :required="otherField.required"
                          :disabled="otherField.disabled"
                          v-bind="otherField.attrs"
                        >
                        <div v-for="fieldError in otherField.errors" :key="fieldError" class="invalid-feedback d-block">{{ fieldError }}</div>
                      </div>
                    </template>
                  </div>

                  <div v-else-if="field.name !== 'last_name'" class="form-group">
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
                      :class="fieldInputClass(field)"
                      :required="field.required"
                      :disabled="field.disabled"
                      v-bind="field.attrs"
                    >
                    <small v-if="fieldHelpText(field)" class="form-text text-muted">
                      {{ fieldHelpText(field) }}
                      <template v-if="field.name === 'username'"> Example: <code>alice-smith42</code></template>
                    </small>
                    <div v-for="fieldError in field.errors" :key="fieldError" class="invalid-feedback d-block">{{ fieldError }}</div>
                  </div>
                </template>
              </div>

              <div class="card-footer d-flex justify-content-end">
                <button type="submit" class="btn btn-primary" title="Create your account">Register</button>
              </div>
            </form>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>