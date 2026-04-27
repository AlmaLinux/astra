<script setup lang="ts">
import { computed, onMounted, ref, type ComponentPublicInstance } from "vue";

import { fetchMembershipRequestFormPayload, type MembershipRequestFormBootstrap, type MembershipRequestFormField, type MembershipRequestFormPayload } from "./types";

const props = defineProps<{
  bootstrap: MembershipRequestFormBootstrap;
}>();

const payload = ref<MembershipRequestFormPayload | null>(props.bootstrap.initialPayload);
const loadError = ref("");
const formRef = ref<HTMLFormElement | null>(null);
const touchedFields = new Set<string>();
const fieldElements = new Map<string, HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>();

const membershipTypeField = computed(() => payload.value?.form.fields.find((field) => field.name === "membership_type") ?? null);
const selectedMembershipType = computed(() => membershipTypeField.value?.value || "");
const selectedMembershipCategory = computed(() => {
  for (const group of membershipTypeField.value?.optionGroups || []) {
    const option = group.options.find((candidate) => candidate.value === selectedMembershipType.value);
    if (option) {
      return option.category;
    }
  }
  if (selectedMembershipType.value === "mirror") {
    return "mirror";
  }
  return "";
});

const showIndividualQuestions = computed(() => selectedMembershipCategory.value === "individual" || (!selectedMembershipCategory.value && selectedMembershipType.value !== "mirror"));
const showSponsorshipQuestions = computed(() => selectedMembershipCategory.value === "sponsorship");
const showMirrorQuestions = computed(() => selectedMembershipCategory.value === "mirror" || selectedMembershipType.value === "mirror");

const individualFields = computed(() => payload.value?.form.fields.filter((field) => field.name === "q_contributions") ?? []);
const sponsorshipFields = computed(() => payload.value?.form.fields.filter((field) => field.name === "q_sponsorship_details") ?? []);
const mirrorFields = computed(
  () => payload.value?.form.fields.filter((field) => field.name.startsWith("q_") && field.name !== "q_contributions" && field.name !== "q_sponsorship_details") ?? [],
);

const passthroughFieldAttrs = new Set([
  "accept",
  "autocomplete",
  "autocapitalize",
  "autocorrect",
  "dirname",
  "enterkeyhint",
  "inputmode",
  "list",
  "maxlength",
  "minlength",
  "pattern",
  "placeholder",
  "readonly",
  "spellcheck",
  "step",
]);

function setFieldElement(name: string) {
  return (element: Element | ComponentPublicInstance | null): void => {
    if (element === null) {
      fieldElements.delete(name);
      return;
    }
    if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement) {
      fieldElements.set(name, element);
    }
  };
}

function dynamicRequired(fieldName: string): boolean {
  if (fieldName === "membership_type") {
    return true;
  }
  if (fieldName === "q_contributions") {
    return showIndividualQuestions.value;
  }
  if (fieldName === "q_sponsorship_details") {
    return showSponsorshipQuestions.value;
  }
  if (fieldName === "q_domain" || fieldName === "q_pull_request") {
    return showMirrorQuestions.value;
  }
  return false;
}

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:") && !!url.hostname;
  } catch {
    return false;
  }
}

function isBareDomain(value: string): boolean {
  return !!value && !/\s/.test(value) && value.includes(".") && /^[A-Za-z0-9.-]+$/.test(value);
}

function validateMirrorFields(): void {
  const domain = fieldElements.get("q_domain");
  const pullRequest = fieldElements.get("q_pull_request");
  if (!showMirrorQuestions.value) {
    domain?.setCustomValidity("");
    pullRequest?.setCustomValidity("");
    return;
  }

  if (domain instanceof HTMLInputElement) {
    const value = domain.value.trim();
    if (!value) {
      domain.setCustomValidity("");
    } else if (value.includes("://")) {
      domain.setCustomValidity(isHttpUrl(value) ? "" : "Enter a valid http(s) URL.");
    } else {
      domain.setCustomValidity(isBareDomain(value) ? "" : "Enter a valid domain name.");
    }
  }

  if (pullRequest instanceof HTMLInputElement) {
    const value = pullRequest.value.trim();
    pullRequest.setCustomValidity(!value || isHttpUrl(value) ? "" : "Enter a valid http(s) URL.");
  }
}

function applyInvalidState(field: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement): void {
  if (field.willValidate) {
    field.classList.toggle("is-invalid", !field.checkValidity());
  } else {
    field.classList.remove("is-invalid");
  }
}

function validateField(name: string): void {
  validateMirrorFields();
  const field = fieldElements.get(name);
  if (!field) {
    return;
  }
  applyInvalidState(field);
}

function handleBlur(name: string): void {
  touchedFields.add(name);
  validateField(name);
}

function handleInput(name: string): void {
  if (name === "membership_type") {
    validateMirrorFields();
  }
  if (touchedFields.has(name) || fieldElements.get(name)?.classList.contains("is-invalid")) {
    validateField(name);
  }
}

function fieldClasses(field: MembershipRequestFormField): string {
  return field.attrs.class || "form-control";
}

function fieldRows(field: MembershipRequestFormField): number {
  return Number.parseInt(field.attrs.rows || "4", 10);
}

function fieldControlAttrs(field: MembershipRequestFormField): Record<string, string> {
  const attrs: Record<string, string> = {};
  for (const [name, value] of Object.entries(field.attrs)) {
    if (name === "class" || name === "rows") {
      continue;
    }
    if (passthroughFieldAttrs.has(name) || name.startsWith("aria-") || name.startsWith("data-")) {
      attrs[name] = value;
    }
  }
  return attrs;
}

function showField(field: MembershipRequestFormField): boolean {
  if (field.name === "membership_type") {
    return true;
  }
  if (field.name === "q_contributions") {
    return showIndividualQuestions.value;
  }
  if (field.name === "q_sponsorship_details") {
    return showSponsorshipQuestions.value;
  }
  return showMirrorQuestions.value;
}

function handleSubmit(event: Event): void {
  validateMirrorFields();
  if (formRef.value) {
    formRef.value.classList.add("was-validated");
    if (!formRef.value.checkValidity()) {
      event.preventDefault();
      event.stopPropagation();
    }
  }
}

async function loadPayload(): Promise<void> {
  if (payload.value !== null || !props.bootstrap.apiUrl) {
    return;
  }

  try {
    payload.value = await fetchMembershipRequestFormPayload(props.bootstrap.apiUrl);
  } catch {
    loadError.value = "Unable to load membership request form right now.";
  }
}

onMounted(async () => {
  await loadPayload();
  validateMirrorFields();
});
</script>

<template>
  <div data-membership-request-form-vue-root>
    <div v-if="loadError" class="alert alert-danger" role="alert">{{ loadError }}</div>
    <div v-else-if="!payload" class="text-muted">Loading membership request form...</div>
    <template v-else>
      <div class="d-flex align-items-center justify-content-between flex-wrap mb-3" style="gap: .5rem;">
        <h1 class="m-0">{{ bootstrap.pageTitle }}</h1>
      </div>

      <div class="card">
        <div class="card-body">
          <template v-if="payload.noTypesAvailable">
            <div class="alert alert-success mt-3" role="alert">
              <strong>Thank you for your support of AlmaLinux!</strong>
              <p>You already hold all available membership types - there are no additional memberships available for you to apply for at this time.</p>
            </div>
            <a :href="bootstrap.cancelUrl" class="btn btn-secondary">Back to your profile</a>
          </template>

          <template v-else>
            <div class="mb-3 text-muted">
              Membership is subject to confirmation of eligibility.
              Your request will be reviewed by the Membership Committee.
            </div>

            <div v-if="payload.prefillTypeUnavailableName" class="alert alert-info" role="alert">
              You already hold an active <strong>{{ payload.prefillTypeUnavailableName }}</strong> membership.
              Please select from the available options below.
            </div>

            <form
              ref="formRef"
              :action="bootstrap.submitUrl"
              method="post"
              :class="payload.form.isBound ? 'needs-validation was-validated' : 'needs-validation'"
              novalidate
              @submit="handleSubmit"
            >
              <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken || ''">

              <div v-if="payload.form.nonFieldErrors.length" class="alert alert-danger" role="alert">
                <div v-for="errorItem in payload.form.nonFieldErrors" :key="errorItem">{{ errorItem }}</div>
              </div>

              <template v-for="field in payload.form.fields" :key="field.name">
                <div v-if="field.name === 'membership_type'" class="form-group">
                  <label :for="field.id">
                    {{ field.label }}
                    <span v-if="dynamicRequired(field.name)" class="form-required-indicator text-danger font-weight-bold ml-1" title="Required" aria-hidden="true">*</span>
                    <span v-if="dynamicRequired(field.name)" class="sr-only">Required</span>
                  </label>
                  <select
                    :id="field.id"
                    :ref="setFieldElement(field.name)"
                    v-model="field.value"
                    :name="field.name"
                    :class="fieldClasses(field)"
                    v-bind="fieldControlAttrs(field)"
                    :disabled="field.disabled"
                    :required="dynamicRequired(field.name)"
                    @input="handleInput(field.name)"
                    @change="handleInput(field.name)"
                    @blur="handleBlur(field.name)"
                  >
                    <template v-for="(group, groupIndex) in field.optionGroups" :key="`${field.name}-${groupIndex}`">
                      <optgroup v-if="group.label" :label="group.label">
                        <option v-for="option in group.options" :key="option.value" :value="option.value" :disabled="option.disabled">{{ option.label }}</option>
                      </optgroup>
                      <template v-else>
                        <option v-for="option in group.options" :key="option.value" :value="option.value" :disabled="option.disabled">{{ option.label }}</option>
                      </template>
                    </template>
                  </select>
                  <div v-for="fieldError in field.errors" :key="fieldError" class="invalid-feedback d-block">{{ fieldError }}</div>
                </div>
              </template>

              <div data-test="questions-individual" class="mt-3" :style="showIndividualQuestions ? '' : 'display: none'">
                <h5>Individual Membership</h5>
                <div v-for="field in individualFields" :key="field.name" class="form-group">
                  <label :for="field.id">
                    {{ field.label }}
                    <span v-if="dynamicRequired(field.name)" class="form-required-indicator text-danger font-weight-bold ml-1" title="Required" aria-hidden="true">*</span>
                    <span v-if="dynamicRequired(field.name)" class="sr-only">Required</span>
                  </label>
                  <textarea
                    :id="field.id"
                    :ref="setFieldElement(field.name)"
                    v-model="field.value"
                    :name="field.name"
                    :rows="fieldRows(field)"
                    :class="fieldClasses(field)"
                    v-bind="fieldControlAttrs(field)"
                    :disabled="field.disabled"
                    :required="dynamicRequired(field.name)"
                    @input="handleInput(field.name)"
                    @change="handleInput(field.name)"
                    @blur="handleBlur(field.name)"
                  />
                  <div v-for="fieldError in field.errors" :key="fieldError" class="invalid-feedback d-block">{{ fieldError }}</div>
                </div>
              </div>

              <div data-test="questions-sponsorship" class="mt-3" :style="showSponsorshipQuestions ? '' : 'display: none'">
                <h5>Sponsorship</h5>
                <div v-for="field in sponsorshipFields" :key="field.name" class="form-group">
                  <label :for="field.id">
                    {{ field.label }}
                    <span v-if="dynamicRequired(field.name)" class="form-required-indicator text-danger font-weight-bold ml-1" title="Required" aria-hidden="true">*</span>
                    <span v-if="dynamicRequired(field.name)" class="sr-only">Required</span>
                  </label>
                  <textarea
                    :id="field.id"
                    :ref="setFieldElement(field.name)"
                    v-model="field.value"
                    :name="field.name"
                    :rows="fieldRows(field)"
                    :class="fieldClasses(field)"
                    v-bind="fieldControlAttrs(field)"
                    :disabled="field.disabled"
                    :required="dynamicRequired(field.name)"
                    @input="handleInput(field.name)"
                    @change="handleInput(field.name)"
                    @blur="handleBlur(field.name)"
                  />
                  <div v-for="fieldError in field.errors" :key="fieldError" class="invalid-feedback d-block">{{ fieldError }}</div>
                </div>
              </div>

              <div data-test="questions-mirror" class="mt-3" :style="showMirrorQuestions ? '' : 'display: none'">
                <h5>Mirror Membership</h5>
                <div v-for="field in mirrorFields" :key="field.name" v-show="showField(field)" class="form-group">
                  <label :for="field.id">
                    {{ field.label }}
                    <span v-if="dynamicRequired(field.name)" class="form-required-indicator text-danger font-weight-bold ml-1" title="Required" aria-hidden="true">*</span>
                    <span v-if="dynamicRequired(field.name)" class="sr-only">Required</span>
                  </label>
                  <textarea
                    v-if="field.widget === 'textarea'"
                    :id="field.id"
                    :ref="setFieldElement(field.name)"
                    v-model="field.value"
                    :name="field.name"
                    :rows="fieldRows(field)"
                    :class="fieldClasses(field)"
                    v-bind="fieldControlAttrs(field)"
                    :disabled="field.disabled"
                    :required="dynamicRequired(field.name)"
                    @input="handleInput(field.name)"
                    @change="handleInput(field.name)"
                    @blur="handleBlur(field.name)"
                  />
                  <input
                    v-else
                    :id="field.id"
                    :ref="setFieldElement(field.name)"
                    v-model="field.value"
                    :name="field.name"
                    type="text"
                    :class="fieldClasses(field)"
                    v-bind="fieldControlAttrs(field)"
                    :disabled="field.disabled"
                    :required="dynamicRequired(field.name)"
                    @input="handleInput(field.name)"
                    @change="handleInput(field.name)"
                    @blur="handleBlur(field.name)"
                  >
                  <small v-if="field.helpText" class="form-text text-muted">{{ field.helpText }}</small>
                  <div v-for="fieldError in field.errors" :key="fieldError" class="invalid-feedback d-block">{{ fieldError }}</div>
                </div>
              </div>

              <div class="mt-4 mb-3 text-muted">
                By clicking the "Submit request" button below, you acknowledge and agree that, when accepted by AlmaLinux OS Foundation, this application represents a binding contract between the parties and commits the applicant to comply with all the terms and conditions of AlmaLinux OS Foundation's Bylaws and such rules and policies as the Board of Directors and/or committees may from time to time adopt. Additionally, the applicant hereby acknowledges and consents to the terms set forth in the <a :href="bootstrap.privacyPolicyUrl">AlmaLinux OS Foundation Privacy Policy</a>.
              </div>

              <div class="d-flex justify-content-between flex-wrap mt-3" style="gap: .5rem;">
                <a :href="bootstrap.cancelUrl" class="btn btn-secondary" title="Return without changes">Cancel</a>
                <button type="submit" class="btn btn-success" title="Submit membership request">Submit request</button>
              </div>
            </form>
          </template>
        </div>
      </div>
    </template>
  </div>
</template>