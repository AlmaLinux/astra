<script setup lang="ts">
import type { PendingFilterOption } from "../types";

defineProps<{
  selectedFilter: string;
  options: PendingFilterOption[];
}>();

const emit = defineEmits<{
  (event: "change", value: string): void;
}>();
</script>

<template>
  <form method="get" class="form-inline" aria-label="Filter membership requests" @submit.prevent>
    <label for="requests-filter" class="text-muted small mb-0 ml-2">Filter</label>
    <select
      id="requests-filter"
      name="filter"
      class="custom-select custom-select-sm ml-2"
      :value="selectedFilter"
      aria-label="Filter requests"
      @change="emit('change', ($event.target as HTMLSelectElement).value)"
    >
      <option v-for="option in options" :key="option.value" :value="option.value">
        {{ option.label }}{{ option.count >= 0 ? ` (${option.count})` : '' }}
      </option>
    </select>
  </form>
</template>