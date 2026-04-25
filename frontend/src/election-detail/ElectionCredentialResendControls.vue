<script setup lang="ts">
import { ref } from "vue";

import type { ElectionCredentialResendBootstrap } from "./types";

const props = defineProps<{
  bootstrap: ElectionCredentialResendBootstrap;
}>();

const username = ref("");
const errors = ref<string[]>([]);
const isSubmittingAll = ref(false);
const isSubmittingSingle = ref(false);

function readCsrfToken(): string {
  for (const cookie of document.cookie.split(";")) {
    const trimmed = cookie.trim();
    if (trimmed.startsWith("csrftoken=")) {
      return decodeURIComponent(trimmed.slice("csrftoken=".length));
    }
  }
  return "";
}

async function submit(targetUsername: string, mode: "all" | "single"): Promise<void> {
  if (mode == "all") {
    isSubmittingAll.value = true;
  } else {
    isSubmittingSingle.value = true;
  }
  errors.value = [];

  try {
    const response = await fetch(props.bootstrap.sendMailCredentialsApiUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": readCsrfToken(),
      },
      body: JSON.stringify({ username: targetUsername }),
    });

    const payload = (await response.json().catch(() => ({ errors: ["Unable to prepare the credential resend right now."] }))) as {
      redirect_url?: string;
      errors?: string[];
    };

    if (!response.ok || !payload.redirect_url) {
      errors.value = payload.errors && payload.errors.length > 0 ? payload.errors : ["Unable to prepare the credential resend right now."];
      return;
    }

    window.location.assign(payload.redirect_url);
  } catch {
    errors.value = ["Unable to prepare the credential resend right now."];
  } finally {
    if (mode == "all") {
      isSubmittingAll.value = false;
    } else {
      isSubmittingSingle.value = false;
    }
  }
}

async function submitAll(): Promise<void> {
  await submit("", "all");
}

async function submitSingle(): Promise<void> {
  await submit(username.value, "single");
}
</script>

<template>
  <div data-election-credential-resend-vue-root>
    <div v-if="errors.length > 0" class="alert alert-danger" role="alert">
      <p v-for="error in errors" :key="error" class="mb-0">{{ error }}</p>
    </div>

    <div class="mb-3">
      <form class="mb-0" @submit.prevent="submitAll">
        <button type="submit" class="btn btn-outline-primary btn-sm" title="Resend credentials to all eligible voters">
          Resend all credentials
        </button>
      </form>
    </div>

    <form class="mb-0" @submit.prevent="submitSingle">
      <label class="sr-only" for="resend-credential-username">Username</label>
      <div class="input-group input-group-sm">
        <input
          id="resend-credential-username"
          v-model="username"
          type="text"
          name="username"
          class="form-control"
          placeholder="username"
          list="eligible-voter-usernames"
          autocomplete="off"
        >
        <div class="input-group-append">
          <button type="submit" class="btn btn-outline-primary" title="Resend credential to the selected user">
            Resend voting credential
          </button>
        </div>
      </div>
      <datalist v-if="bootstrap.eligibleUsernames.length > 0" id="eligible-voter-usernames">
        <option v-for="eligibleUsername in bootstrap.eligibleUsernames" :key="eligibleUsername" :value="eligibleUsername"></option>
      </datalist>
    </form>
  </div>
</template>