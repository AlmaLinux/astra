<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";

import "./membershipNotes.css";

import { useMembershipNotes } from "../composables/useMembershipNotes";
import type { NoteEntry, NoteGroup } from "../types";
import ContactedEmailModal from "./ContactedEmailModal.vue";

const props = defineProps<{
  requestId: number;
  summaryUrl: string;
  detailUrl: string;
  addUrl: string;
  csrfToken: string;
  nextUrl: string;
  canView: boolean;
  canWrite: boolean;
  canVote: boolean;
  initialOpen?: boolean;
  targetType?: string;
  target?: string;
}>();

const message = ref("");
const messagesEl = ref<HTMLElement | null>(null);
const {
  summary,
  details,
  isOpen,
  isDetailsLoading,
  isSummaryLoading,
  isSummaryUnavailable,
  error,
  postError,
  noteCount,
  fetchSummary,
  fetchDetails,
  toggle,
  postNote,
  clearError,
} = useMembershipNotes({
  requestId: props.requestId,
  summaryUrl: props.summaryUrl,
  detailUrl: props.detailUrl,
  addUrl: props.addUrl,
  csrfToken: props.csrfToken,
  nextUrl: props.nextUrl,
  canView: props.canView,
  initialOpen: props.initialOpen ?? false,
  targetType: props.targetType,
  target: props.target,
});

function scrollToBottom(): void {
  nextTick(() => {
    if (messagesEl.value) {
      messagesEl.value.scrollTop = messagesEl.value.scrollHeight;
    }
  });
}

onMounted(async () => {
  await fetchSummary();
  if (isOpen.value) {
    await fetchDetails();
    scrollToBottom();
  }
});

async function onToggle(): Promise<void> {
  await toggle();
  if (isOpen.value) {
    await fetchDetails();
    scrollToBottom();
  }
}

async function submit(noteAction: string): Promise<void> {
  await postNote(noteAction, message.value);
  if (!postError.value) {
    message.value = "";
    scrollToBottom();
  }
}

function onComposerKeydown(event: KeyboardEvent): void {
  if (event.key !== "Enter") {
    return;
  }
  if (!event.ctrlKey && !event.metaKey) {
    return;
  }
  event.preventDefault();
  void submit("message");
}

const voteText = computed(() => summary.value?.current_user_vote ?? "");
const approvalBadgeClass = computed(() => {
  if (!props.canVote) {
    return "badge-success";
  }
  return voteText.value === "approve" ? "badge-warning" : "badge-success";
});
const disapprovalBadgeClass = computed(() => {
  if (!props.canVote) {
    return "badge-danger";
  }
  return voteText.value === "disapprove" ? "badge-warning" : "badge-danger";
});
const collapseIconClass = computed(() => (isOpen.value ? "fa-minus" : "fa-plus"));
const footerError = computed(() => postError.value || error.value);
const noteCountLabel = computed(() => {
  if (isSummaryUnavailable.value) {
    return "!";
  }
  if (isSummaryLoading.value || summary.value === null) {
    return "...";
  }
  return String(noteCount.value);
});
const noteCountTitle = computed(() => {
  if (isSummaryUnavailable.value) {
    return "Note summary unavailable";
  }
  if (isSummaryLoading.value || summary.value === null) {
    return "Loading note summary";
  }
  return `${noteCount.value} Messages`;
});
const approvalsLabel = computed(() => (summary.value === null ? "..." : String(summary.value.approvals ?? 0)));
const disapprovalsLabel = computed(() => (summary.value === null ? "..." : String(summary.value.disapprovals ?? 0)));
const contactedEmailEntries = computed(() => {
  const entries: Array<{ key: string; contactedEmail: NoteEntry["contacted_email"] }> = [];
  const seen = new Set<string>();

  for (const group of details.value?.groups ?? []) {
    for (const entry of group.entries ?? []) {
      if (!entry.contacted_email) {
        continue;
      }
      const modalId = entryModalId(entry);
      if (seen.has(modalId)) {
        continue;
      }
      seen.add(modalId);
      entries.push({
        key: `${modalId}-${entry.note_id ?? entry.label ?? entries.length}`,
        contactedEmail: entry.contacted_email,
      });
    }
  }

  return entries;
});

function entryModalId(entry: NoteEntry): string {
  return `membership-email-modal-${props.requestId}-${entry.contacted_email?.email_id ?? 'email'}`;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatLineBreaks(value: string): string {
  return escapeHtml(value).replace(/\r\n|\r|\n/g, "<br>");
}

function bubbleStyle(entry: NoteEntry): string | undefined {
  if (!entry.bubble_style && !entry.is_custos) {
    return undefined;
  }
  const parts: string[] = [];
  if (entry.bubble_style) {
    parts.push(entry.bubble_style.trim().replace(/;$/, ""));
  }
  if (entry.is_custos) {
    parts.push("border: 1px dashed rgba(0,0,0,0.15)");
  }
  return parts.join("; ");
}

function groupAvatar(group: NoteGroup): string {
  return group.avatar_url || "/static/core/images/almalinux-logo.svg";
}

function groupAvatarClass(group: NoteGroup): string {
  if (group.avatar_url) {
    return group.avatar_kind === "user" ? "direct-chat-img img-circle" : "direct-chat-img";
  }
  return "direct-chat-img";
}

function groupAvatarStyle(group: NoteGroup): string | undefined {
  if (!group.avatar_url) {
    return undefined;
  }
  if (group.avatar_kind === "user") {
    return "object-fit:cover;";
  }
  return "object-fit: cover; background: #fff;";
}

function groupAvatarAlt(group: NoteGroup): string {
  if (!group.avatar_url) {
    return "user image";
  }
  return group.avatar_kind === "user" ? "User Avatar" : "Astra Custodia";
}
</script>

<template>
  <div
    :id="`membership-notes-container-${requestId}`"
    :data-membership-notes-container="String(requestId)"
  >
    <div
      :id="`membership-notes-card-${requestId}`"
      class="card card-primary card-outline direct-chat direct-chat-primary mb-0"
      :class="{ 'collapsed-card': !isOpen }"
      :data-membership-notes-card="String(requestId)"
    >
      <div
        class="card-header membership-notes-header-compact"
        :data-membership-notes-toggle="String(requestId)"
        :data-membership-notes-header="String(requestId)"
        role="button"
        tabindex="0"
        aria-label="Toggle membership notes"
        style="cursor: pointer;"
        @click="onToggle()"
        @keydown.enter.prevent="onToggle()"
        @keydown.space.prevent="onToggle()"
      >
        <h3 class="card-title membership-notes-title text-sm text-truncate">Membership Committee Notes</h3>
        <div class="card-tools">
          <span
            :class="['badge', isSummaryUnavailable ? 'badge-warning' : 'badge-primary']"
            :title="noteCountTitle"
            :data-membership-notes-count="String(requestId)"
          >{{ noteCountLabel }}</span>
          <span v-if="canVote" :class="['badge', approvalBadgeClass]" title="Approvals" :data-membership-notes-approvals="String(requestId)">
            <i class="fas fa-thumbs-up"></i> {{ approvalsLabel }}
          </span>
          <span v-if="canVote" :class="['badge', disapprovalBadgeClass]" title="Disapprovals" :data-membership-notes-disapprovals="String(requestId)">
            <i class="fas fa-thumbs-down"></i> {{ disapprovalsLabel }}
          </span>
          <button
            type="button"
            class="btn btn-tool"
            data-card-widget="collapse"
            aria-label="Collapse"
            title="Expand or collapse notes"
            :data-membership-notes-collapse="String(requestId)"
            @click.stop="onToggle()"
          >
            <i class="fas" :class="collapseIconClass" aria-hidden="true"></i>
          </button>
        </div>
      </div>
      <div v-if="isOpen" class="card-body">
        <div class="direct-chat-messages" :data-membership-notes-messages="String(requestId)" style="max-height: 260px;" ref="messagesEl">
          <div v-if="isDetailsLoading" class="text-muted small">Loading notes...</div>
          <div v-else-if="details?.groups.length">
            <div
              v-for="(group, groupIndex) in details.groups"
              :key="`${group.username}-${group.timestamp_display}`"
              class="direct-chat-msg"
              :class="{ right: group.is_self, 'mb-3': groupIndex + 1 < details.groups.length }"
            >
              <div class="direct-chat-infos clearfix">
                <template v-if="group.is_self">
                  <span class="direct-chat-name float-right">{{ group.display_username }}</span>
                  <span class="direct-chat-timestamp float-left">
                    {{ group.timestamp_display }}
                    <a v-if="group.membership_request_id && group.membership_request_url" :href="group.membership_request_url" class="text-muted ml-1">(req. #{{ group.membership_request_id }})</a>
                  </span>
                </template>
                <template v-else>
                  <span class="direct-chat-name float-left">{{ group.display_username }}</span>
                  <span class="direct-chat-timestamp float-right">
                    <a v-if="group.membership_request_id && group.membership_request_url" :href="group.membership_request_url" class="text-muted mr-1">(req. #{{ group.membership_request_id }})</a>
                    {{ group.timestamp_display }}
                  </span>
                </template>
              </div>
              <img :class="groupAvatarClass(group)" :src="groupAvatar(group)" :alt="groupAvatarAlt(group)" :style="groupAvatarStyle(group)">
              <div class="membership-notes-bubbles">
                <div v-for="entry in group.entries" :key="`${group.username}-${entry.note_id ?? entry.label ?? entry.rendered_html}`">
                  <div
                    v-if="entry.kind === 'message' && !entry.is_self && entry.bubble_style"
                    class="direct-chat-text membership-notes-bubble"
                    :style="bubbleStyle(entry)"
                    v-html="entry.rendered_html"
                  ></div>
                  <div
                    v-else-if="entry.kind === 'message'"
                    class="direct-chat-text"
                    :class="{ 'membership-notes-self-bubble': entry.is_self }"
                    v-html="entry.rendered_html"
                  ></div>
                  <div v-else class="direct-chat-text membership-notes-bubble bg-light text-dark" :style="bubbleStyle(entry)">
                    <i class="fas mr-1" :class="entry.icon || 'fa-bolt'"></i>
                    {{ entry.label || '' }}
                    <button
                      v-if="entry.contacted_email"
                      type="button"
                      class="btn btn-link btn-sm p-0 ml-2"
                      data-toggle="modal"
                      aria-label="View email"
                      :data-target="`#${entryModalId(entry)}`"
                    >View email</button>
                    <div v-if="entry.request_resubmitted_diff_rows?.length" class="mt-2">
                      <details v-for="diffRow in entry.request_resubmitted_diff_rows" :key="diffRow.question" class="mt-1">
                        <summary>{{ diffRow.question }}</summary>
                        <div class="small text-muted mt-2">Previous response</div>
                        <div data-request-resubmitted-old v-html="formatLineBreaks(diffRow.old_value)"></div>
                        <div class="small text-muted mt-2">Updated response</div>
                        <div data-request-resubmitted-new v-html="formatLineBreaks(diffRow.new_value)"></div>
                      </details>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div v-else class="text-muted small">No notes yet.</div>
        </div>
      </div>
      <div class="card-footer">
        <div v-if="footerError" class="alert alert-danger py-2 px-3 mb-2" role="alert" aria-live="polite">
          <div class="d-flex align-items-start justify-content-between" style="gap: .75rem;">
            <div>{{ footerError }}</div>
            <button type="button" class="close" aria-label="Dismiss" title="Dismiss error message" :data-membership-notes-error-close="String(requestId)" @click="clearError()">
              <span aria-hidden="true">&times;</span>
            </button>
          </div>
        </div>
        <form v-if="canWrite && isOpen" :data-membership-notes-form="String(requestId)" @submit.prevent="submit('message')">
          <div class="input-group">
            <textarea
              name="message"
              class="form-control"
              rows="2"
              placeholder="Type a note..."
              v-model="message"
              @keydown="onComposerKeydown"
            ></textarea>
            <div class="input-group-append">
              <button
                type="submit"
                class="btn btn-light border"
                data-note-action="message"
                title="Send (Ctrl+Enter)"
                aria-label="Send note"
                @click.prevent="submit('message')"
              >
                <i class="fas fa-paper-plane" aria-hidden="true"></i>
              </button>
            </div>
          </div>
          <div v-if="canVote" class="d-flex w-100 mt-1" role="group" aria-label="Vote actions">
            <button
              type="button"
              class="btn btn-light border btn-sm flex-fill py-1"
              data-note-action="vote_approve"
              title="Vote to approve"
              aria-label="Vote approve"
              @click="submit('vote_approve')"
            >
              <i class="fas fa-thumbs-up text-success" aria-hidden="true"></i>
            </button>
            <button
              type="button"
              class="btn btn-light border btn-sm flex-fill py-1"
              data-note-action="vote_disapprove"
              title="Vote to disapprove"
              aria-label="Vote disapprove"
              @click="submit('vote_disapprove')"
            >
              <i class="fas fa-thumbs-down text-danger" aria-hidden="true"></i>
            </button>
          </div>
        </form>
      </div>
    </div>
    <div :id="`membership-notes-modals-${requestId}`" :data-membership-notes-modals="String(requestId)">
      <ContactedEmailModal
        v-for="modalEntry in contactedEmailEntries"
        :key="modalEntry.key"
        :request-id="requestId"
        :contacted-email="modalEntry.contactedEmail"
      />
    </div>
  </div>
</template>