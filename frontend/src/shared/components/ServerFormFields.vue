<script setup lang="ts">
import { computed } from "vue";

export interface ServerFormField {
  name: string;
  id: string;
  widget: string;
  value: string;
  required: boolean;
  disabled: boolean;
  errors: string[];
  attrs: Record<string, string>;
}

// Renders a server-described form's hidden inputs, form-wide errors and labelled inputs.
const props = defineProps<{
  form: { nonFieldErrors: string[]; fields: ServerFormField[] };
  labels: Record<string, string>;
  helpTexts?: Record<string, string>;
}>();

const hiddenFields = computed(() => props.form.fields.filter((field) => field.widget === "hidden"));
const visibleFields = computed(() => props.form.fields.filter((field) => field.widget !== "hidden"));
</script>

<template>
  <input
    v-for="field in hiddenFields"
    :key="field.name"
    type="hidden"
    :name="field.name"
    :id="field.id"
    :value="field.value"
  >

  <div v-if="form.nonFieldErrors.length" class="alert alert-danger" role="alert">
    <div v-for="errorItem in form.nonFieldErrors" :key="errorItem">{{ errorItem }}</div>
  </div>

  <div v-for="field in visibleFields" :key="field.name" class="form-group">
    <label :for="field.id">
      {{ labels[field.name] || field.name }}
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
    <small v-if="helpTexts?.[field.name]" class="form-text text-muted">{{ helpTexts[field.name] }}</small>
    <div v-for="fieldError in field.errors" :key="fieldError" class="invalid-feedback d-block">{{ fieldError }}</div>
  </div>
</template>
