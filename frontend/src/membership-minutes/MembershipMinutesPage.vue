<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { renderMinutesHtml } from "./renderMinutes";
import type { MembershipMinutesBootstrap, MinutesData } from "./types";
import { buildDateRangeSearch, parseDateRangeFromSearch } from "./urlState";

const props = defineProps<{
  bootstrap: MembershipMinutesBootstrap;
}>();

function isoDaysAgo(days: number): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

// Today (UTC), used to prevent selecting an end date in the future.
const today = isoDaysAgo(0);

// A shared link may prefill the range via ?start=&end=; otherwise default to the
// last 30 days.
const initialRange = parseDateRangeFromSearch(window.location.search);
const startDate = ref(initialRange?.start ?? isoDaysAgo(30));
const endDate = ref(initialRange?.end ?? today);

const isLoading = ref(false);
const error = ref<string | null>(null);
const minutesHtml = ref<string | null>(null);
const copied = ref(false);

const contentRef = ref<HTMLElement | null>(null);

const canSubmit = computed(
  () =>
    !isLoading.value &&
    startDate.value !== "" &&
    endDate.value !== "" &&
    startDate.value <= endDate.value &&
    endDate.value <= today,
);

async function generate(): Promise<void> {
  isLoading.value = true;
  error.value = null;
  copied.value = false;
  minutesHtml.value = null;

  // Reflect the current range in the URL so the page can be shared as a link.
  window.history.replaceState(
    null,
    "",
    buildDateRangeSearch(startDate.value, endDate.value, window.location.search),
  );

  try {
    const url = `${props.bootstrap.apiUrl}?start=${encodeURIComponent(startDate.value)}&end=${encodeURIComponent(endDate.value)}`;
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as { error?: string } | null;
      error.value = payload?.error ?? "Failed to generate minutes. Please try again.";
      return;
    }
    const data = (await response.json()) as MinutesData;
    minutesHtml.value = renderMinutesHtml(data);
  } catch {
    error.value = "Failed to generate minutes. Please try again.";
  } finally {
    isLoading.value = false;
  }
}

// When arriving via a shared link with a valid range, generate immediately.
onMounted(() => {
  if (initialRange !== null) {
    void generate();
  }
});

async function copyMinutes(): Promise<void> {
  const html = minutesHtml.value;
  const container = contentRef.value;
  if (!html || container === null) {
    return;
  }
  const plain = container.innerText;
  try {
    if (navigator.clipboard && "write" in navigator.clipboard && typeof ClipboardItem !== "undefined") {
      const item = new ClipboardItem({
        "text/html": new Blob([html], { type: "text/html" }),
        "text/plain": new Blob([plain], { type: "text/plain" }),
      });
      await navigator.clipboard.write([item]);
    } else if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(plain);
    } else {
      return;
    }
    copied.value = true;
    window.setTimeout(() => {
      copied.value = false;
    }, 2000);
  } catch {
    error.value = "Could not copy to the clipboard. Select the text manually instead.";
  }
}
</script>

<template>
  <div class="membership-minutes">
    <div class="card">
      <div class="card-body">
        <p class="text-muted">
          Select a date range (both days included). The minutes below are generated from the membership audit log
          and can be copied straight into the minutes document.
        </p>
        <div class="form-row align-items-end">
          <div class="form-group col-sm-4 col-md-3">
            <label for="minutes-start">Start date</label>
            <input id="minutes-start" v-model="startDate" type="date" class="form-control" :max="endDate" />
          </div>
          <div class="form-group col-sm-4 col-md-3">
            <label for="minutes-end">End date</label>
            <input id="minutes-end" v-model="endDate" type="date" class="form-control" :min="startDate" :max="today" />
          </div>
          <div class="form-group col-sm-4 col-md-3">
            <button type="button" class="btn btn-primary" :disabled="!canSubmit" @click="generate">
              <span v-if="isLoading">Generating…</span>
              <span v-else>Generate minutes</span>
            </button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="error" class="alert alert-danger" role="alert">{{ error }}</div>

    <div v-if="minutesHtml !== null" class="card">
      <div class="card-header d-flex justify-content-between align-items-center">
        <span>Generated minutes</span>
        <button type="button" class="btn btn-sm btn-default" @click="copyMinutes">
          {{ copied ? "Copied!" : "Copy to clipboard" }}
        </button>
      </div>
      <div class="card-body">
        <!-- eslint-disable-next-line vue/no-v-html -- content is built and escaped in renderMinutes.ts -->
        <div ref="contentRef" class="minutes-content" v-html="minutesHtml"></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.membership-minutes .card {
  margin-bottom: 1rem;
}

.minutes-content :deep(ul) {
  margin-bottom: 0;
}
</style>
