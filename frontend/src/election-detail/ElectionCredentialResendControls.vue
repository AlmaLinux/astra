<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";

import type { ElectionCredentialResendBootstrap } from "./types";

type JQueryCollection = {
  select2?: (options?: unknown) => void;
  trigger?: (event: string) => void;
  val?: (value?: string | null) => unknown;
  on?: (event: string, callback: (...args: unknown[]) => void) => void;
};

type JQueryFunction = ((target: Element | string) => JQueryCollection) & {
  fn?: {
    select2?: unknown;
  };
};

const props = defineProps<{
  bootstrap: ElectionCredentialResendBootstrap;
}>();

const selectRef = ref<HTMLSelectElement | null>(null);
const username = ref("");
const errors = ref<string[]>([]);
const successMessage = ref("");
const isSubmittingAll = ref(false);
const isSubmittingSingle = ref(false);
const isSubmitting = computed(() => isSubmittingAll.value || isSubmittingSingle.value);
const select2Initialized = ref(false);
const confirmMode = ref<"all" | "single" | null>(null);

const confirmMessage = computed(() => {
  if (confirmMode.value === "all") {
    const count = props.bootstrap.eligibleUsernames.length;
    return `Send voting credentials to all ${count} eligible voter${count === 1 ? "" : "s"}?`;
  }
  if (confirmMode.value === "single") {
    return `Send voting credentials to ${username.value}?`;
  }
  return "";
});

function requestConfirm(mode: "all" | "single"): void {
  confirmMode.value = mode;
}

function cancelConfirm(): void {
  confirmMode.value = null;
}

async function confirmAndSend(): Promise<void> {
  const mode = confirmMode.value;
  if (!mode) {
    return;
  }
  confirmMode.value = null;
  if (mode === "all") {
    await submit("", "all");
  } else {
    await submit(username.value, "single");
  }
}

function getJQuery(): JQueryFunction | null {
  const maybeJQuery = (window as typeof window & { jQuery?: JQueryFunction; $?: JQueryFunction }).jQuery
    || (window as typeof window & { jQuery?: JQueryFunction; $?: JQueryFunction }).$;
  return maybeJQuery || null;
}

function supportsSelect2(jquery: JQueryFunction | null): jquery is JQueryFunction {
  return Boolean(jquery?.fn && typeof jquery.fn.select2 === "function");
}

function initSelect2(): void {
  const el = selectRef.value;
  if (!el || select2Initialized.value) {
    return;
  }

  const jquery = getJQuery();
  if (!supportsSelect2(jquery)) {
    return;
  }

  select2Initialized.value = true;

  jquery(el).select2?.({
    width: "100%",
    allowClear: true,
    placeholder: "Select an eligible voter",
  });
}

function onSelectionChange(event: Event): void {
  const target = event.target as HTMLSelectElement | null;
  username.value = target?.value || "";
}

function resetSelect2(): void {
  const el = selectRef.value;
  if (!el) {
    return;
  }

  const jquery = getJQuery();
  if (supportsSelect2(jquery)) {
    jquery(el).val?.(null);
    jquery(el).trigger?.("change.select2");
    return;
  }

  el.value = "";
}

watch(
  () => props.bootstrap.eligibleUsernames,
  async (usernames) => {
    if (usernames.length > 0 && !select2Initialized.value) {
      await nextTick();
      initSelect2();
    }
  },
  { flush: "post", immediate: true },
);

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
  if (isSubmitting.value) {
    return;
  }

  if (mode == "all") {
    isSubmittingAll.value = true;
  } else {
    isSubmittingSingle.value = true;
  }
  errors.value = [];
  successMessage.value = "";

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
      errors?: string[];
      message?: string;
      ok?: boolean;
    };

    if (!response.ok || payload.ok !== true || !payload.message) {
      errors.value = payload.errors && payload.errors.length > 0 ? payload.errors : ["Unable to prepare the credential resend right now."];
      return;
    }

    successMessage.value = payload.message;
    if (mode == "single") {
      username.value = "";
      resetSelect2();
    }
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
  requestConfirm("all");
}

async function submitSingle(): Promise<void> {
  requestConfirm("single");
}
</script>

<template>
  <div data-election-credential-resend-vue-root>
    <div v-if="successMessage" class="alert alert-success" role="status">
      <p class="mb-0">{{ successMessage }}</p>
    </div>

    <div v-if="errors.length > 0" class="alert alert-danger" role="alert">
      <p v-for="error in errors" :key="error" class="mb-0">{{ error }}</p>
    </div>

    <div class="d-flex align-items-center" style="gap: .5rem;">
      <form class="d-flex align-items-center mb-0" style="gap: .5rem; min-width: 0; flex: 1 1 auto;" @submit.prevent="submitSingle">
        <label class="sr-only" for="resend-credential-username">Username</label>
        <div style="min-width: 0; flex: 1 1 auto;">
          <select
            id="resend-credential-username"
            ref="selectRef"
            name="username"
            class="form-control"
            :disabled="isSubmitting"
            @change="onSelectionChange"
          >
            <option value=""></option>
            <option v-for="eligibleUsername in bootstrap.eligibleUsernames" :key="eligibleUsername" :value="eligibleUsername">
              {{ eligibleUsername }}
            </option>
          </select>
        </div>
        <button type="submit" class="btn btn-outline-primary btn-sm" title="Resend credential to the selected user" :disabled="isSubmitting || !username" style="white-space: nowrap;">
          Resend voting credential
        </button>
      </form>

      <form class="mb-0 ml-auto flex-shrink-0" @submit.prevent="submitAll">
        <button type="submit" class="btn btn-outline-primary btn-sm" title="Resend credentials to all eligible voters" :disabled="isSubmitting" style="white-space: nowrap;">
          Resend all credentials
        </button>
      </form>
    </div>

    <div
      v-if="confirmMode"
      class="modal d-block"
      tabindex="-1"
      role="dialog"
      style="background: rgba(0, 0, 0, 0.5);"
      @click.self="cancelConfirm"
    >
      <div class="modal-dialog" role="document">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title">Confirm credential resend</h5>
            <button type="button" class="close" aria-label="Close" @click="cancelConfirm">
              <span aria-hidden="true">&times;</span>
            </button>
          </div>
          <div class="modal-body">
            <p class="mb-0">{{ confirmMessage }}</p>
          </div>
          <div class="modal-footer d-flex justify-content-between">
            <button type="button" class="btn btn-secondary" @click="cancelConfirm">Cancel</button>
            <button type="button" class="btn btn-danger" :disabled="isSubmitting" @click="confirmAndSend">Send</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>