import { computed, onBeforeUnmount, ref, type ComputedRef, type Ref } from "vue";

import type { MailProgress, MailProgressResponse } from "./types";

const POLL_INTERVAL_MS = 1000;

export interface MailProgressTracker {
  progress: Ref<MailProgress | null>;
  percent: ComputedRef<number>;
  /** The run reached a terminal state: done, failed or stalled. */
  finished: ComputedRef<boolean>;
  failed: ComputedRef<boolean>;
  /** Show the seeded record and poll until the run reaches a terminal state. */
  track: (initial: MailProgress) => void;
  /** Adopt whatever run the server already has for this operator, if any. */
  adopt: () => Promise<void>;
  reset: () => void;
}

/**
 * Follow a bulk election email run started by this operator.
 *
 * Polling stops on its own once the run finishes, when the progress record has
 * expired, or when the component goes away.
 */
export function useMailProgress(progressApiUrl: string, onExpired: () => void): MailProgressTracker {
  const progress = ref<MailProgress | null>(null);
  let pollTimer: number | undefined;

  const percent = computed(() => {
    const current = progress.value;
    if (current === null || current.total <= 0) {
      return 0;
    }
    return Math.min(100, Math.round((current.processed / current.total) * 100));
  });

  const finished = computed(() => progress.value !== null && progress.value.state !== "running");
  const failed = computed(() => progress.value !== null && (progress.value.state === "failed" || progress.value.state === "stalled"));

  function schedulePoll(): void {
    if (finished.value) {
      return;
    }
    pollTimer = window.setTimeout(poll, POLL_INTERVAL_MS);
  }

  async function poll(): Promise<void> {
    try {
      const response = await fetch(progressApiUrl, { credentials: "same-origin", headers: { Accept: "application/json" } });
      const payload = await response.json() as MailProgressResponse;
      if (response.ok) {
        if (payload.mail_progress == null) {
          // The progress record expired; the run is no longer observable.
          onExpired();
          return;
        }
        progress.value = payload.mail_progress;
      }
    } catch {
      // A dropped poll is not fatal: keep polling, and the stalled state on the
      // server side ends the wait if delivery really did stop.
    }
    schedulePoll();
  }

  function track(initial: MailProgress): void {
    progress.value = initial;
    schedulePoll();
  }

  async function adopt(): Promise<void> {
    try {
      const response = await fetch(progressApiUrl, { credentials: "same-origin", headers: { Accept: "application/json" } });
      const payload = await response.json() as MailProgressResponse;
      if (response.ok && payload.mail_progress != null) {
        track(payload.mail_progress);
      }
    } catch {
      // Nothing to show; the run, if any, carries on server-side regardless.
    }
  }

  function reset(): void {
    window.clearTimeout(pollTimer);
    progress.value = null;
  }

  onBeforeUnmount(() => {
    window.clearTimeout(pollTimer);
  });

  return { progress, percent, finished, failed, track, adopt, reset };
}
