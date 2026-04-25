import { computed, ref } from "vue";

import type { NoteDetails, NoteSummary } from "../types";

interface UseMembershipNotesOptions {
  requestId: number;
  summaryUrl: string;
  detailUrl: string;
  addUrl: string;
  csrfToken: string;
  nextUrl: string;
  canView: boolean;
  initialOpen?: boolean;
  targetType?: string;
  target?: string;
}

export function useMembershipNotes(options: UseMembershipNotesOptions) {
  const summary = ref<NoteSummary | null>(null);
  const details = ref<NoteDetails | null>(null);
  const isOpen = ref(options.initialOpen ?? false);
  const isSummaryLoading = ref(false);
  const isDetailsLoading = ref(false);
  const isSummaryUnavailable = ref(false);
  const error = ref("");
  const postError = ref("");

  async function fetchSummary(): Promise<void> {
    if (!options.canView) {
      return;
    }
    isSummaryLoading.value = true;
    isSummaryUnavailable.value = false;
    try {
      const response = await fetch(options.summaryUrl, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const payload = (await response.json()) as NoteSummary;
      if (!response.ok) {
        isSummaryUnavailable.value = true;
        return;
      }
      summary.value = payload;
    } catch {
      isSummaryUnavailable.value = true;
    } finally {
      isSummaryLoading.value = false;
    }
  }

  async function fetchDetails(fetchOptions: { force?: boolean } = {}): Promise<void> {
    const force = fetchOptions.force ?? false;
    if (!options.canView || (!force && details.value !== null)) {
      return;
    }
    isDetailsLoading.value = true;
    error.value = "";
    try {
      const response = await fetch(options.detailUrl, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const payload = (await response.json()) as NoteDetails;
      if (!response.ok) {
        error.value = "Failed to load notes.";
        return;
      }
      details.value = payload;
    } catch {
      error.value = "Failed to load notes.";
    } finally {
      isDetailsLoading.value = false;
    }
  }

  async function toggle(): Promise<void> {
    isOpen.value = !isOpen.value;
  }

  async function postNote(noteAction: string, message: string): Promise<void> {
    postError.value = "";
    const body = new URLSearchParams({
      note_action: noteAction,
      message,
      next: options.nextUrl,
    });
    if (options.targetType && options.target) {
      body.set("target_type", options.targetType);
      body.set("target", options.target);
    }
    const response = await fetch(options.addUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-CSRFToken": options.csrfToken,
        "X-Requested-With": "XMLHttpRequest",
      },
      body: body.toString(),
      credentials: "same-origin",
    });
    const payload = (await response.json()) as { ok?: boolean; error?: string };
    if (!response.ok || !payload.ok) {
      postError.value = payload.error ?? "Failed to add note.";
      return;
    }
    await Promise.all([fetchSummary(), fetchDetails({ force: true })]);
    isOpen.value = true;
  }

  const noteCount = computed(() => summary.value?.note_count ?? 0);

  function clearError(): void {
    error.value = "";
    postError.value = "";
  }

  return {
    summary,
    details,
    isOpen,
    isSummaryLoading,
    isSummaryUnavailable,
    isDetailsLoading,
    error,
    postError,
    noteCount,
    fetchSummary,
    fetchDetails,
    toggle,
    postNote,
    clearError,
  };
}