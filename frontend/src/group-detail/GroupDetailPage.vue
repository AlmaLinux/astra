<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import WidgetGrid from "../shared/components/WidgetGrid.vue";
import WidgetUser from "../shared/components/WidgetUser.vue";
import {
  buildGroupDetailRouteUrl,
  readGroupDetailRouteState,
  type GroupDetailBootstrap,
  type GroupInfoResponse,
  type GroupLeadersResponse,
  type GroupMembersResponse,
  type GroupLeaderGroupItem,
  type GroupLeaderItem,
  type GroupLeaderUserItem,
  type GroupMemberItem,
  type GroupDetailRouteState,
} from "./types";

const props = defineProps<{
  bootstrap: GroupDetailBootstrap;
}>();

const groupInfo = ref<GroupInfoResponse["group"] | null>(null);
const leadersPayload = ref<GroupLeadersResponse["leaders"] | null>(null);
const membersPayload = ref<GroupMembersResponse["members"] | null>(null);
const error = ref("");
const actionError = ref("");
const isLoading = ref(false);
const leadersLoading = ref(false);
const membersLoading = ref(false);
const leadersError = ref("");
const membersError = ref("");
const actionSubmitting = ref(false);
const q = ref("");
const currentPage = ref(1);
const leadersPage = ref(1);
const addMemberUsername = ref("");
const addMemberSuggestions = ref<string[]>([]);
const confirmModalOpen = ref(false);
const pendingAction = ref<{ action: string; username: string }>({ action: "", username: "" });

const membersRows = computed<GroupMemberItem[]>(() => membersPayload.value?.items || []);
const membersCount = computed(() => membersPayload.value?.pagination.count || groupInfo.value?.member_count || 0);
const membersPagination = computed(() => membersPayload.value?.pagination || null);
const leaderItems = computed<GroupLeaderItem[]>(() => leadersPayload.value?.items || []);
const leadersPagination = computed(() => leadersPayload.value?.pagination || null);

function asMember(row: unknown): GroupMemberItem {
  return row as GroupMemberItem;
}

function asLeaderItem(row: unknown): GroupLeaderItem {
  return row as GroupLeaderItem;
}

function asLeaderUser(row: unknown): GroupLeaderUserItem {
  return row as GroupLeaderUserItem;
}

function isLeaderGroup(item: GroupLeaderItem): item is GroupLeaderGroupItem {
  return item.kind === "group";
}

function currentRouteState(): GroupDetailRouteState {
  return {
    pathname: window.location.pathname,
    q: q.value,
    page: currentPage.value,
    leadersPage: leadersPage.value,
  };
}

function applyRouteState(routeState: GroupDetailRouteState): void {
  q.value = routeState.q;
  currentPage.value = routeState.page;
  leadersPage.value = routeState.leadersPage;
}

function syncUrl(pushState: boolean): void {
  const nextUrl = buildGroupDetailRouteUrl(currentRouteState());
  if (pushState) {
    window.history.pushState(null, "", nextUrl);
    return;
  }
  window.history.replaceState(null, "", nextUrl);
}

function buildPageHref(pageNumber: number): string {
  const routeState = currentRouteState();
  routeState.page = pageNumber;
  return buildGroupDetailRouteUrl(routeState);
}

function buildLeadersPageHref(pageNumber: number): string {
  const routeState = currentRouteState();
  routeState.leadersPage = pageNumber;
  return buildGroupDetailRouteUrl(routeState);
}

function isUnsigned(username: string): boolean {
  return groupInfo.value?.unsigned_usernames.includes(username) || false;
}

function sponsorActions(username: string): Array<{
  key: string;
  ariaLabel: string;
  title: string;
  buttonClass: string;
  iconClass: string;
  disabled?: boolean;
  onClick: () => void;
}> {
  if (!groupInfo.value?.is_sponsor || username === props.bootstrap.currentUsername) {
    return [];
  }

  return [
    {
      key: `demote-${username}`,
      ariaLabel: "Remove Team Lead",
      title: "Remove Team Lead role from this user",
      buttonClass: "btn btn-outline-danger btn-sm",
      iconClass: "fas fa-person-arrow-down-to-line",
      disabled: actionSubmitting.value,
      onClick: () => openConfirmModal("demote_sponsor", username),
    },
  ];
}

function memberActions(member: GroupMemberItem): Array<{
  key: string;
  ariaLabel: string;
  title: string;
  buttonClass: string;
  iconClass: string;
  disabled?: boolean;
  onClick: () => void;
}> {
  if (!groupInfo.value?.is_sponsor) {
    return [];
  }

  const username = member.username;
  const isExistingSponsor = member.is_leader === true;

  return [
    {
      key: `remove-${username}`,
      ariaLabel: "Remove member",
      title: "Remove this member from the group",
      buttonClass: "btn btn-outline-danger btn-sm",
      iconClass: "fas fa-user-minus",
      disabled: actionSubmitting.value,
      onClick: () => openConfirmModal("remove_member", username),
    },
    ...(isExistingSponsor
      ? []
      : [
          {
            key: `promote-${username}`,
            ariaLabel: "Promote to Team Lead",
            title: "Promote this member to Team Lead",
            buttonClass: "btn btn-outline-primary btn-sm",
            iconClass: "fas fa-person-arrow-up-from-line",
            disabled: actionSubmitting.value,
            onClick: () => openConfirmModal("promote_member", username),
          },
        ]),
  ];
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

function membersApiQuery(): string {
  const params = new URLSearchParams();
  if (q.value) {
    params.set("q", q.value);
  }
  if (currentPage.value > 1) {
    params.set("page", String(currentPage.value));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

function leadersApiQuery(): string {
  const params = new URLSearchParams();
  if (leadersPage.value > 1) {
    params.set("page", String(leadersPage.value));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

async function load(pushState: boolean): Promise<void> {
  isLoading.value = true;
  error.value = "";

  try {
    const loaded = await Promise.all([
      loadGroupInfo(),
      loadLeaders(false),
      loadMembers(false),
    ]);

    if (loaded.includes(false)) {
      error.value = "Unable to load group details right now.";
      return;
    }

    syncUrl(pushState);
  } catch {
    error.value = "Unable to load group details right now.";
  } finally {
    isLoading.value = false;
  }
}

async function loadGroupInfo(): Promise<boolean> {
  try {
    const response = await fetch(props.bootstrap.infoApiUrl, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!response.ok) {
      return false;
    }

    const groupInfoData = (await response.json()) as GroupInfoResponse;
    groupInfo.value = groupInfoData.group;
    return true;
  } catch {
    return false;
  }
}

async function loadLeaders(pushState: boolean): Promise<boolean> {
  leadersLoading.value = true;
  leadersError.value = "";

  try {
    const response = await fetch(`${props.bootstrap.leadersApiUrl}${leadersApiQuery()}`, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!response.ok) {
      leadersError.value = "Unable to load team leads right now.";
      return false;
    }

    const leadersData = (await response.json()) as GroupLeadersResponse;
    leadersPayload.value = leadersData.leaders;
    leadersPage.value = leadersData.leaders.pagination.page;
    if (pushState) {
      syncUrl(true);
    }
    return true;
  } catch {
    leadersError.value = "Unable to load team leads right now.";
    return false;
  } finally {
    leadersLoading.value = false;
  }
}

async function loadMembers(pushState: boolean): Promise<boolean> {
  membersLoading.value = true;
  membersError.value = "";

  try {
    const response = await fetch(`${props.bootstrap.membersApiUrl}${membersApiQuery()}`, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!response.ok) {
      membersError.value = "Unable to load members right now.";
      return false;
    }

    const membersData = (await response.json()) as GroupMembersResponse;
    membersPayload.value = membersData.members;
    q.value = membersData.members.q;
    currentPage.value = membersData.members.pagination.page;
    if (pushState) {
      syncUrl(true);
    }
    return true;
  } catch {
    membersError.value = "Unable to load members right now.";
    return false;
  } finally {
    membersLoading.value = false;
  }
}

async function postAction(action: string, username?: string): Promise<void> {
  actionSubmitting.value = true;
  actionError.value = "";

  try {
    const response = await fetch(props.bootstrap.actionUrl, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),
      },
      credentials: "same-origin",
      body: JSON.stringify({ action, username: username || "" }),
    });

    const responsePayload = (await response.json()) as { ok?: boolean; error?: string };
    if (!response.ok || responsePayload.ok === false) {
      actionError.value = responsePayload.error || "Unable to apply this action right now.";
      return;
    }

    await load(false);
  } catch {
    actionError.value = "Unable to apply this action right now.";
  } finally {
    actionSubmitting.value = false;
  }
}

async function onMemberPageChange(pageNumber: number): Promise<void> {
  currentPage.value = pageNumber;
  await loadMembers(true);
}

async function onLeaderPageChange(pageNumber: number): Promise<void> {
  leadersPage.value = pageNumber;
  await loadLeaders(true);
}

async function onMemberSearch(): Promise<void> {
  currentPage.value = 1;
  await loadMembers(true);
}

async function clearMemberSearch(): Promise<void> {
  q.value = "";
  await onMemberSearch();
}

async function addMemberAction(event: Event): Promise<void> {
  event.preventDefault();
  if (!addMemberUsername.value.trim()) {
    return;
  }
  await postAction("add_member", addMemberUsername.value.trim());
  addMemberUsername.value = "";
  addMemberSuggestions.value = [];
}

function openConfirmModal(action: string, username?: string): void {
  pendingAction.value = { action, username: username || "" };
  confirmModalOpen.value = true;
}

function closeConfirmModal(): void {
  confirmModalOpen.value = false;
  pendingAction.value = { action: "", username: "" };
}

async function confirmAndAct(): Promise<void> {
  const action = pendingAction.value.action;
  if (!action) {
    return;
  }
  const username = pendingAction.value.username;
  closeConfirmModal();
  await postAction(action, username || undefined);
}

function confirmModalId(): string {
  if (pendingAction.value.action === "leave") {
    return "leave-group-modal";
  }
  if (pendingAction.value.action === "stop_sponsoring") {
    return "stop-sponsoring-modal";
  }
  if (pendingAction.value.action === "remove_member") {
    return "remove-member-modal";
  }
  return "group-action-confirm-modal";
}

function confirmModalTitle(): string {
  if (pendingAction.value.action === "leave") {
    return "Leave group?";
  }
  if (pendingAction.value.action === "stop_sponsoring") {
    return "Stop being a Team Lead?";
  }
  if (pendingAction.value.action === "remove_member") {
    return "Remove member?";
  }
  if (pendingAction.value.action === "promote_member") {
    return "Promote member to Team Lead?";
  }
  if (pendingAction.value.action === "demote_sponsor") {
    return "Demote Team Lead?";
  }
  return "Confirm action";
}

function confirmModalBody(): string {
  const username = pendingAction.value.username;
  if (pendingAction.value.action === "leave") {
    return "Are you sure you want to leave this group?";
  }
  if (pendingAction.value.action === "stop_sponsoring") {
    return "Are you sure you want to stop being a Team Lead for this group?";
  }
  if (pendingAction.value.action === "remove_member") {
    return `Remove ${username} from this group?`;
  }
  if (pendingAction.value.action === "promote_member") {
    return `Promote ${username} to Team Lead for this group?`;
  }
  if (pendingAction.value.action === "demote_sponsor") {
    return `Remove Team Lead access for ${username}?`;
  }
  return "Are you sure you want to continue?";
}

let addMemberSuggestionsTimer: number | null = null;
let addMemberSuggestionsAbortController: AbortController | null = null;

function setAddMemberSuggestions(usernames: string[]): void {
  addMemberSuggestions.value = usernames.slice(0, 12);
}

function queueAddMemberSuggestionsFetch(): void {
  const query = addMemberUsername.value.trim();
  if (addMemberSuggestionsTimer !== null) {
    window.clearTimeout(addMemberSuggestionsTimer);
  }
  addMemberSuggestionsTimer = window.setTimeout(() => {
    void fetchAddMemberSuggestions(query);
  }, 180);
}

async function fetchAddMemberSuggestions(query: string): Promise<void> {
  if (!query) {
    setAddMemberSuggestions([]);
    return;
  }
  if (addMemberSuggestionsAbortController !== null) {
    addMemberSuggestionsAbortController.abort();
  }
  addMemberSuggestionsAbortController = new AbortController();

  try {
    const response = await fetch(`/search/?q=${encodeURIComponent(query)}`, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
      signal: addMemberSuggestionsAbortController.signal,
    });
    if (!response.ok) {
      return;
    }
    const payloadUsers = (await response.json()) as { users?: Array<{ username?: string }> };
    const usernames = Array.isArray(payloadUsers.users)
      ? payloadUsers.users
          .map((item) => String(item?.username || "").trim())
          .filter((username) => username.length > 0)
      : [];
    setAddMemberSuggestions(usernames);
  } catch (error: unknown) {
    if (error instanceof DOMException && error.name === "AbortError") {
      return;
    }
  }
}

onMounted(async () => {
  applyRouteState(readGroupDetailRouteState(window.location.href));
  window.addEventListener("popstate", () => {
    applyRouteState(readGroupDetailRouteState(window.location.href));
    void load(false);
  });
  await load(false);
});
</script>

<template>
  <div data-group-detail-vue-root>
    <div v-if="error" class="text-danger mb-3">{{ error }}</div>
    <div v-else-if="isLoading && !groupInfo" class="text-muted mb-3">Loading group details...</div>
    <template v-else-if="groupInfo && leadersPayload && membersPayload">
      <!-- Header: Group name with member count and Leave button -->
      <div class="d-flex justify-content-between align-items-center flex-wrap gap-2 mb-3">
        <h1 class="m-0">
          Group: {{ groupInfo.cn }}
          <small class="text-muted">({{ membersCount }} member{{ membersCount === 1 ? "" : "s" }})</small>
        </h1>
        <button
          v-if="groupInfo.is_member"
          type="button"
          class="btn btn-outline-danger"
          :disabled="actionSubmitting"
          @click="openConfirmModal('leave')"
        >Leave group</button>
      </div>

      <div v-if="actionError" class="alert alert-danger">{{ actionError }}</div>

      <!-- Main content with responsive layout: col-lg-8 for sponsors, col-lg-12 for non-sponsors -->
      <div class="row">
        <!-- Main content column -->
        <div :class="groupInfo.is_sponsor ? 'col-lg-8' : 'col-lg-12'">
          <!-- Group Info Card -->
          <div class="card">
            <div class="card-header d-flex align-items-center" style="gap: .5rem;">
              <h3 class="card-title mb-0">Group info</h3>
              <div v-if="groupInfo.edit_url && groupInfo.is_sponsor" class="card-tools ml-auto">
                <a :href="groupInfo.edit_url" class="btn btn-sm btn-outline-primary" title="Edit group details">
                  <i class="fas fa-edit mr-1"></i> Edit group
                </a>
              </div>
            </div>
            <ul class="list-group list-group-flush">
              <li v-if="groupInfo.description" class="list-group-item d-flex justify-content-between align-items-center">
                <strong class="profile-attr-label mr-2" title="Description"><i class="fas fa-align-left"></i> Description</strong>
                <div class="profile-attr-value text-end">{{ groupInfo.description }}</div>
              </li>

              <li v-if="groupInfo.fas_url" class="list-group-item d-flex justify-content-between align-items-center">
                <strong class="profile-attr-label" title="URL"><i class="fas fa-link"></i> URL</strong>
                <div class="profile-attr-value text-end"><a :href="groupInfo.fas_url" rel="noopener" target="_blank">{{ groupInfo.fas_url }}</a></div>
              </li>

              <li v-if="groupInfo.fas_mailing_list" class="list-group-item d-flex justify-content-between align-items-center">
                <strong class="profile-attr-label" title="Mailing list"><i class="fas fa-envelope"></i> Mailing list</strong>
                <div class="profile-attr-value text-end"><a :href="`mailto:${groupInfo.fas_mailing_list}`">{{ groupInfo.fas_mailing_list }}</a></div>
              </li>

              <li v-if="groupInfo.fas_irc_channels.length" class="list-group-item d-flex justify-content-between">
                <strong class="profile-attr-label" title="Chat channels"><i class="fas fa-comments"></i> Chat</strong>
                <div class="profile-attr-value text-end">
                  <div v-for="channel in groupInfo.fas_irc_channels" :key="channel" class="mb-0 text-monospace">{{ channel }}</div>
                </div>
              </li>

              <li v-if="groupInfo.fas_discussion_url" class="list-group-item d-flex justify-content-between align-items-center">
                <strong class="profile-attr-label" title="Discussion URL"><i class="far fa-comment-dots"></i> Discussion</strong>
                <div class="profile-attr-value text-end"><a :href="groupInfo.fas_discussion_url" rel="noopener" target="_blank">{{ groupInfo.fas_discussion_url }}</a></div>
              </li>

              <li v-if="groupInfo.required_agreements.length" class="list-group-item d-flex justify-content-between">
                <strong class="profile-attr-label" title="Required agreements"><i class="fas fa-file-signature"></i> Agreements</strong>
                <div class="profile-attr-value text-end">
                  <div v-for="a in groupInfo.required_agreements" :key="a.cn" class="mb-1">
                    <span v-if="a.signed" class="badge badge-success">{{ a.cn }} (Signed)</span>
                    <a v-else :href="a.detail_url" class="badge badge-warning">{{ a.cn }} (Unsigned)</a>
                  </div>
                  <div class="small text-muted mt-1">
                    Sign agreements in <a href="/settings/#agreements">Settings → Agreements</a>.
                  </div>
                </div>
              </li>

              <li v-if="!groupInfo.description && !groupInfo.fas_url && !groupInfo.fas_mailing_list && !groupInfo.fas_irc_channels.length && !groupInfo.fas_discussion_url && !groupInfo.required_agreements.length" class="list-group-item text-muted text-center py-4">No group info available.</li>
            </ul>
          </div>

        </div>

        <!-- Right sidebar for sponsors only (col-lg-4) -->
        <div v-if="groupInfo.is_sponsor" class="col-lg-4">
          <div class="card">
            <div class="card-header">
              <h3 class="card-title">Team membership</h3>
            </div>
            <div class="card-body">
              <form class="mb-2" @submit.prevent="addMemberAction">
                <div class="input-group">
                  <input
                    v-model="addMemberUsername"
                    type="text"
                    list="sponsor-user-suggestions"
                    name="username"
                    class="form-control"
                    placeholder="Add member by username"
                    autocomplete="off"
                    :disabled="actionSubmitting"
                    @input="queueAddMemberSuggestionsFetch"
                  >
                  <button class="btn btn-primary" type="submit" :disabled="actionSubmitting" title="Add this user to the group">Add</button>
                </div>
              </form>

              <datalist id="sponsor-user-suggestions">
                <option v-for="username in addMemberSuggestions" :key="username" :value="username"></option>
              </datalist>

              <button
                type="button"
                class="btn btn-outline-secondary btn-sm w-100"
                :disabled="actionSubmitting"
                @click="openConfirmModal('stop_sponsoring')"
                title="Remove your Team Lead role in this group"
              >Stop being a Team Lead</button>

              <small v-if="groupInfo.required_agreements.length" class="text-muted d-block mt-2">
                Users must have signed required agreements before being added.
              </small>
            </div>
          </div>
        </div>
      </div>

      <!-- Team Leads section (full width below columns) -->
      <div v-if="leaderItems.length" class="card">
        <div class="card-header">
          <h3 class="card-title">Team Lead{{ (leadersPagination?.count || 0) === 1 ? "" : "s" }}</h3>
        </div>
        <div class="card-body">
          <WidgetGrid
            :items="leaderItems"
            :is-loading="leadersLoading"
            :error="leadersError"
            empty-message="No Team Leads found."
            :pagination="leadersPagination"
            :build-page-href="buildLeadersPageHref"
            @page-change="onLeaderPageChange"
          >
            <template #item="{ item }">
              <a v-if="isLeaderGroup(asLeaderItem(item))" :href="`/group/${asLeaderItem(item).cn}/`" class="card" style="text-decoration: none; color: inherit;">
                <div class="card-body">
                  <h5 class="card-title">{{ asLeaderItem(item).cn }}</h5>
                  <p class="card-text small text-muted">Group</p>
                </div>
              </a>
              <WidgetUser
                v-else
                :username="asLeaderUser(item).username"
                :full-name="asLeaderUser(item).full_name"
                :avatar-url="asLeaderUser(item).avatar_url"
                :dimmed="isUnsigned(asLeaderUser(item).username)"
                :secondary-text="isUnsigned(asLeaderUser(item).username) ? 'Unsigned' : undefined"
                :actions="sponsorActions(asLeaderUser(item).username)"
              />
            </template>
          </WidgetGrid>
        </div>
      </div>

      <!-- Members section (full width) -->
      <div class="card">
        <div class="card-header d-flex align-items-center" style="gap: .5rem;">
          <h3 class="card-title mb-0">Member{{ membersCount === 1 ? "" : "s" }}</h3>
          <div class="card-tools ml-auto">
            <form method="get" class="input-group input-group-sm" style="width: 220px;" @submit.prevent="onMemberSearch">
              <input
                v-model="q"
                type="text"
                name="q"
                class="form-control float-right"
                placeholder="Search members..."
                aria-label="Search members"
              >
              <div class="input-group-append">
                <button
                  v-if="q"
                  type="button"
                  class="btn btn-default"
                  aria-label="Clear search"
                  title="Clear search filter"
                  @click="clearMemberSearch"
                >
                  <i class="fas fa-times" />
                </button>
                <button type="submit" class="btn btn-default" aria-label="Search" title="Search group members">
                  <i class="fas fa-search" />
                </button>
              </div>
            </form>
          </div>
        </div>

        <div class="card-body">
          <WidgetGrid
            :items="membersRows"
            :is-loading="membersLoading"
            :error="membersError"
            empty-message="No members found."
            :pagination="membersPagination"
            :build-page-href="buildPageHref"
            @page-change="onMemberPageChange"
          >
            <template #item="{ item }">
              <WidgetUser
                :username="asMember(item).username"
                :full-name="asMember(item).full_name"
                :avatar-url="asMember(item).avatar_url"
                :dimmed="isUnsigned(asMember(item).username)"
                :secondary-text="isUnsigned(asMember(item).username) ? 'Unsigned' : undefined"
                :actions="memberActions(asMember(item))"
              />
            </template>
          </WidgetGrid>
        </div>
      </div>

      <div
        v-if="confirmModalOpen"
        :id="confirmModalId()"
        class="modal d-block"
        tabindex="-1"
        role="dialog"
        style="background: rgba(0, 0, 0, 0.5);"
        @click.self="closeConfirmModal"
      >
        <div class="modal-dialog" role="document">
          <div class="modal-content">
            <div class="modal-header">
              <h5 class="modal-title">{{ confirmModalTitle() }}</h5>
              <button type="button" class="close" aria-label="Close" @click="closeConfirmModal">
                <span aria-hidden="true">&times;</span>
              </button>
            </div>
            <div class="modal-body">
              <p class="mb-0">{{ confirmModalBody() }}</p>
            </div>
            <div class="modal-footer d-flex justify-content-between">
              <button type="button" class="btn btn-secondary" @click="closeConfirmModal">Cancel</button>
              <button type="button" class="btn btn-primary" :disabled="actionSubmitting" @click="confirmAndAct">Confirm</button>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
