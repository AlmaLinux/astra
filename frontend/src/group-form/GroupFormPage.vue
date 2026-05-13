<script setup lang="ts">
import { nextTick, onMounted, reactive, ref } from "vue";

import { type GroupFormBootstrap, type GroupFormGetResponse, type GroupFormPutResponse } from "./types";

declare global {
  interface Window {
    ChatChannelsEditor?: {
      initAll?: (scope?: ParentNode) => void;
    };
  }
}

const props = defineProps<{
  bootstrap: GroupFormBootstrap;
}>();

const isLoading = ref(false);
const isSubmitting = ref(false);
const loadError = ref("");
const submitError = ref("");
const chatChannelsTextarea = ref<HTMLTextAreaElement | null>(null);
const fieldErrors = reactive<Record<string, string[]>>({});

const formState = reactive({
  cn: "",
  description: "",
  fas_url: "",
  fas_mailing_list: "",
  fas_discussion_url: "",
  fas_irc_channels: "",
});

function clearFieldErrors(): void {
  for (const key of Object.keys(fieldErrors)) {
    delete fieldErrors[key];
  }
}

function getCsrfToken(): string {
  const cookies = document.cookie.split(";");
  for (const cookie of cookies) {
    const trimmed = cookie.trim();
    if (trimmed.startsWith("csrftoken=")) {
      return decodeURIComponent(trimmed.slice("csrftoken=".length));
    }
  }
  return "";
}

function applyPayload(payload: GroupFormGetResponse): void {
  formState.cn = payload.group.cn;
  formState.description = payload.group.description;
  formState.fas_url = payload.group.fas_url;
  formState.fas_mailing_list = payload.group.fas_mailing_list;
  formState.fas_discussion_url = payload.group.fas_discussion_url;
  formState.fas_irc_channels = payload.group.fas_irc_channels.join("\n");
}

function syncChatChannelsFromTextarea(): void {
  if (chatChannelsTextarea.value) {
    formState.fas_irc_channels = chatChannelsTextarea.value.value;
  }
}

async function loadForm(): Promise<void> {
  isLoading.value = true;
  loadError.value = "";

  try {
    const response = await fetch(props.bootstrap.apiUrl, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });

    if (!response.ok) {
      loadError.value = "Unable to load group editor right now.";
      return;
    }

    const payload = (await response.json()) as GroupFormGetResponse;
    applyPayload(payload);
  } catch {
    loadError.value = "Unable to load group editor right now.";
  } finally {
    isLoading.value = false;
  }
}

async function submit(): Promise<void> {
  syncChatChannelsFromTextarea();
  isSubmitting.value = true;
  submitError.value = "";
  clearFieldErrors();

  try {
    const response = await fetch(props.bootstrap.apiUrl, {
      method: "PUT",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),
      },
      credentials: "same-origin",
      body: JSON.stringify({
        description: formState.description,
        fas_url: formState.fas_url,
        fas_mailing_list: formState.fas_mailing_list,
        fas_discussion_url: formState.fas_discussion_url,
        fas_irc_channels: formState.fas_irc_channels,
      }),
    });

    const payload = (await response.json()) as GroupFormPutResponse;
    if (!response.ok || payload.ok === false) {
      if (payload.errors && typeof payload.errors === "object") {
        for (const [key, value] of Object.entries(payload.errors)) {
          if (Array.isArray(value)) {
            fieldErrors[key] = value.map((item) => String(item));
          } else if (typeof value === "string" && value) {
            fieldErrors[key] = [value];
          }
        }
      }
      submitError.value = payload.error || "Unable to save group info right now.";
      return;
    }

    window.location.assign(props.bootstrap.detailUrl);
  } catch {
    submitError.value = "Unable to save group info right now.";
  } finally {
    isSubmitting.value = false;
  }
}

onMounted(async () => {
  await loadForm();
  await nextTick();
  window.ChatChannelsEditor?.initAll?.(document);
});
</script>

<template>
  <div data-group-form-vue-root>
    <div v-if="loadError" class="alert alert-danger">{{ loadError }}</div>
    <div v-else-if="isLoading" class="text-muted">Loading group editor...</div>
    <div v-else class="row">
      <div class="col-lg-8">
        <div class="card card-outline card-primary">
          <div class="card-header">
            <h3 class="card-title">Group info</h3>
          </div>

          <form @submit.prevent="submit" novalidate>
            <div class="card-body">
              <div class="form-group">
                <label for="group-description">Description</label>
                <textarea id="group-description" v-model="formState.description" name="description" rows="3" class="form-control" />
                <div v-for="errorItem in fieldErrors.description || []" :key="errorItem" class="invalid-feedback d-block">{{ errorItem }}</div>
              </div>

              <div class="form-group">
                <label for="group-fas-url">URL</label>
                <input id="group-fas-url" v-model="formState.fas_url" name="fas_url" type="url" class="form-control">
                <div v-for="errorItem in fieldErrors.fas_url || []" :key="errorItem" class="invalid-feedback d-block">{{ errorItem }}</div>
              </div>

              <div class="form-group">
                <label for="group-mailing-list">Mailing list</label>
                <input id="group-mailing-list" v-model="formState.fas_mailing_list" name="fas_mailing_list" type="text" class="form-control">
                <div v-for="errorItem in fieldErrors.fas_mailing_list || []" :key="errorItem" class="invalid-feedback d-block">{{ errorItem }}</div>
              </div>

              <div class="form-group">
                <label for="group-discussion-url">Discussion URL</label>
                <input id="group-discussion-url" v-model="formState.fas_discussion_url" name="fas_discussion_url" type="url" class="form-control">
                <div v-for="errorItem in fieldErrors.fas_discussion_url || []" :key="errorItem" class="invalid-feedback d-block">{{ errorItem }}</div>
              </div>

              <div class="form-group mb-0">
                <label for="group-irc-channels">Chat channels</label>
                <div class="help mb-2">
                  Chat channels:
                  <ul class="mb-0">
                    <li>Mattermost: <strong>~channel</strong> or <strong>~channel:server.name:team</strong> (default <strong>{{ props.bootstrap.chatDefaults.mattermostServer }}</strong>, team <strong>{{ props.bootstrap.chatDefaults.mattermostTeam }}</strong>)</li>
                    <li>IRC: <strong>#channel</strong> or <strong>#channel:server.name</strong> (default <strong>{{ props.bootstrap.chatDefaults.ircServer }}</strong>)</li>
                    <li>Matrix: <strong>matrix:/#channel</strong> or <strong>matrix://server.name/#channel</strong> (default <strong>{{ props.bootstrap.chatDefaults.matrixServer }}</strong>)</li>
                  </ul>
                </div>
                <div
                  id="group-chat-channels-widget"
                  class="d-none js-chat-channels-editor"
                  data-textarea-id="group-irc-channels"
                  data-fallback-id="group-chat-channels-fallback"
                  :data-mattermost-default-server="props.bootstrap.chatDefaults.mattermostServer"
                  :data-mattermost-default-team="props.bootstrap.chatDefaults.mattermostTeam"
                  :data-irc-default-server="props.bootstrap.chatDefaults.ircServer"
                  :data-matrix-default-server="props.bootstrap.chatDefaults.matrixServer"
                >
                  <div class="table-responsive">
                    <table class="table table-sm table-bordered align-middle mb-2">
                      <tbody></tbody>
                    </table>
                  </div>
                  <button type="button" class="btn btn-sm btn-outline-secondary js-chat-channels-add" title="Add another chat channel">Add channel</button>
                </div>
                <div id="group-chat-channels-fallback">
                  <textarea id="group-irc-channels" ref="chatChannelsTextarea" v-model="formState.fas_irc_channels" name="fas_irc_channels" rows="4" class="form-control" />
                </div>
                <div v-for="errorItem in fieldErrors.fas_irc_channels || []" :key="errorItem" data-chat-channels-error class="invalid-feedback d-block mt-2">{{ errorItem }}</div>
              </div>

              <div v-if="submitError" class="mt-3 alert alert-danger">{{ submitError }}</div>
            </div>

            <div class="card-footer d-flex justify-content-end" style="gap: .5rem;">
              <a class="btn btn-outline-secondary" :href="props.bootstrap.detailUrl">Cancel</a>
              <button type="submit" class="btn btn-primary" :disabled="isSubmitting">
                <i class="fas fa-save mr-1" />
                Save
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  </div>
</template>
