<script setup lang="ts">
import { onMounted, ref } from "vue";

import WidgetGrid from "../shared/components/WidgetGrid.vue";
import WidgetUser from "../shared/components/WidgetUser.vue";
import type { UsersBootstrap, UsersGridItem, UsersGridResponse, UsersPagination } from "./types";

const props = defineProps<{
  bootstrap: UsersBootstrap;
}>();

const items = ref<UsersGridItem[]>([]);
const pagination = ref<UsersPagination | null>(null);
const emptyMessage = ref("No users found.");
const isLoading = ref(false);
const error = ref("");
const currentSearch = ref(window.location.search);

function profileUrl(username: string): string {
  return `/user/${encodeURIComponent(username)}/`;
}

function buildPageHref(pageNumber: number): string {
  const params = new URLSearchParams(currentSearch.value);
  if (pageNumber <= 1) {
    params.delete("page");
  } else {
    params.set("page", String(pageNumber));
  }
  const query = params.toString();
  return query ? `${window.location.pathname}?${query}` : window.location.pathname;
}

async function loadForQuery(search: string, pushState: boolean): Promise<void> {
  isLoading.value = true;
  error.value = "";

  try {
    const response = await fetch(`${props.bootstrap.usersApiUrl}${search || ""}`, {
      headers: {
        Accept: "application/json",
      },
      credentials: "same-origin",
    });

    if (!response.ok) {
      error.value = "Unable to load users right now.";
      return;
    }

    const payload = (await response.json()) as UsersGridResponse;
    items.value = payload.users;
    pagination.value = payload.pagination;
    currentSearch.value = search;

    if (pushState) {
      window.history.pushState({ usersGrid: true }, "", `${window.location.pathname}${search}`);
    }
  } catch {
    error.value = "Unable to load users right now.";
  } finally {
    isLoading.value = false;
  }
}

async function onPageChange(pageNumber: number): Promise<void> {
  const href = buildPageHref(pageNumber);
  if (!href || href === "#") {
    return;
  }
  const destination = new URL(href, window.location.origin);
  await loadForQuery(destination.search, true);
}

onMounted(async () => {
  window.addEventListener("popstate", () => {
    currentSearch.value = window.location.search;
    void loadForQuery(window.location.search, false);
  });

  await loadForQuery(window.location.search, false);
});
</script>

<template>
  <div data-users-vue-root>
    <WidgetGrid
      :items="items"
      :is-loading="isLoading"
      :error="error"
      :empty-message="emptyMessage"
      :pagination="pagination"
      :build-page-href="buildPageHref"
      @page-change="onPageChange"
    >
      <template #item="{ item }">
        <WidgetUser
          :username="(item as UsersGridItem).username"
          :full-name="(item as UsersGridItem).full_name"
          :avatar-url="(item as UsersGridItem).avatar_url"
          :profile-url="profileUrl((item as UsersGridItem).username)"
        />
      </template>
    </WidgetGrid>
  </div>
</template>
