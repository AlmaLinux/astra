<script setup lang="ts">
import { ref } from "vue";

import type { ElectionVoterSearchBootstrap } from "./types";

const props = defineProps<{
  bootstrap: ElectionVoterSearchBootstrap;
}>();

const value = ref(props.bootstrap.value);

function targetUrl(nextValue: string): string {
  const trimmed = nextValue.trim();
  if (!trimmed) {
    return window.location.pathname;
  }
  const params = new URLSearchParams();
  params.set(props.bootstrap.fieldName, trimmed);
  return `${window.location.pathname}?${params.toString()}`;
}

function submit(): void {
  window.location.assign(targetUrl(value.value));
}

function clear(): void {
  value.value = "";
  window.location.assign(targetUrl(""));
}
</script>

<template>
  <div data-election-voter-search-vue-root>
    <form method="get" class="input-group input-group-sm" :style="{ width: bootstrap.width }" @submit.prevent="submit">
      <input
        :name="bootstrap.fieldName"
        v-model="value"
        type="text"
        class="form-control float-right"
        :placeholder="bootstrap.placeholder"
        :aria-label="bootstrap.ariaLabel"
      >
      <div class="input-group-append">
        <button
          v-if="value"
          type="button"
          class="btn btn-default"
          aria-label="Clear search"
          title="Clear search filter"
          @click="clear"
        >
          <i class="fas fa-times"></i>
        </button>
        <button type="submit" class="btn btn-default" aria-label="Search" :title="bootstrap.submitTitle">
          <i class="fas fa-search"></i>
        </button>
      </div>
    </form>
  </div>
</template>