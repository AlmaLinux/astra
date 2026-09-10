<script setup lang="ts">
import { ref } from "vue";

import type { ElectionStartAutomationBootstrap } from "./types";

const props = defineProps<{ bootstrap: ElectionStartAutomationBootstrap }>();
const enabled = ref(props.bootstrap.autoStartEnabled);
const busy = ref(false);
const error = ref("");

function csrfToken(): string {
  return document.cookie.split(";").map((value) => value.trim()).find((value) => value.startsWith("csrftoken="))?.slice(10) || "";
}

async function post(url: string): Promise<void> {
  busy.value = true;
  error.value = "";
  try {
    const response = await fetch(url, { method: "POST", credentials: "same-origin", headers: { Accept: "application/json", "X-CSRFToken": csrfToken() } });
    const payload = await response.json() as { ok: boolean; errors?: string[]; election?: { auto_start_enabled: boolean } };
    if (!response.ok || !payload.ok) {
      error.value = payload.errors?.[0] || "Unable to update election automation.";
      return;
    }
    if (url === props.bootstrap.startApiUrl) {
      window.location.reload();
      return;
    }
    enabled.value = Boolean(payload.election?.auto_start_enabled);
  } catch {
    error.value = "Unable to update election automation.";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div data-election-start-automation-vue-root>
    <p v-if="error" class="alert alert-danger" role="alert">{{ error }}</p>
    <div class="btn-group btn-block" role="group" aria-label="Election start actions">
      <button type="button" class="btn btn-success" :disabled="busy" title="Start the election now" @click="post(bootstrap.startApiUrl)">Start Election</button>
      <button type="button" class="btn btn-outline-success election-automation-toggle" :disabled="busy" :aria-label="enabled ? 'Cancel automatic start' : 'Enable automatic start at the scheduled start time'" :aria-pressed="enabled" :title="enabled ? 'Automatic start is enabled. Click to cancel.' : 'Start automatically at the scheduled start time.'" @click="post(bootstrap.autoStartApiUrl)">
        <i :class="enabled ? 'fas fa-pause' : 'fas fa-play'" aria-hidden="true"></i>
      </button>
    </div>
  </div>
</template>

<style scoped>
.election-automation-toggle {
  flex: 0 0 42px;
}
</style>