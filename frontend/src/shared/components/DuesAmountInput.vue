<script setup lang="ts">
import { computed } from "vue";

import { duesAmountError, formatUsd } from "../membershipPresentation";

// Whole US dollars as typed, or "" for the tier's standard price.
const model = defineModel<string>({ required: true });

const props = defineProps<{
  id: string;
  name?: string;
  defaultUsd: number;
}>();

const error = computed(() => duesAmountError(model.value, props.defaultUsd));

// A number input would accept "10aa", then report it as empty and lose it, so this is
// a text input that keeps only digits.
function onInput(event: Event): void {
  const input = event.target as HTMLInputElement;
  input.value = input.value.replace(/\D/g, "");
  model.value = input.value;
}
</script>

<template>
  <div class="form-group">
    <label :for="id">Annual dues (USD)</label>
    <input
      :id="id"
      :name="name"
      :value="model"
      type="text"
      inputmode="numeric"
      autocomplete="off"
      class="form-control"
      :class="{ 'is-invalid': error }"
      :placeholder="`${formatUsd(defaultUsd)} (standard price)`"
      @input="onInput"
    >
    <div v-if="error" class="invalid-feedback">{{ error }}</div>
    <small class="form-text text-muted">
      Leave empty to charge the standard price of {{ formatUsd(defaultUsd) }}, or enter a lower amount for an in-kind
      sponsor.
    </small>
  </div>
</template>
