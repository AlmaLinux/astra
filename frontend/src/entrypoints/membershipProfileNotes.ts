import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import MembershipNotesCard from "../membership-requests/components/MembershipNotesCard.vue";

export interface MembershipProfileNotesBootstrap {
  summaryUrl: string;
  detailUrl: string;
  addUrl: string;
  csrfToken: string;
  nextUrl: string;
  canView: boolean;
  canWrite: boolean;
  compact: boolean;
}

function readMembershipProfileNotesBootstrap(root: HTMLElement): MembershipProfileNotesBootstrap | null {
  const summaryUrl = root.getAttribute("data-membership-notes-aggregate-summary-url");
  const detailUrl = root.getAttribute("data-membership-notes-aggregate-detail-url");
  const addUrl = root.getAttribute("data-membership-notes-aggregate-add-url");
  const csrfToken = root.getAttribute("data-csrf-token");
  const nextUrl = root.getAttribute("data-next-url");
  const canViewStr = root.getAttribute("data-can-view");
  const canWriteStr = root.getAttribute("data-can-write");
  const compactStr = root.getAttribute("data-compact");

  if (
    !summaryUrl ||
    !detailUrl ||
    !addUrl ||
    !csrfToken ||
    canViewStr === null ||
    canWriteStr === null
  ) {
    return null;
  }

  return {
    summaryUrl,
    detailUrl,
    addUrl,
    csrfToken,
    nextUrl: nextUrl || "",
    canView: canViewStr === "true",
    canWrite: canWriteStr === "true",
    compact: compactStr === "true",
  };
}

export function mountMembershipProfileNotes(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readMembershipProfileNotesBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(MembershipNotesCard, {
    requestId: 0, // Unused for aggregate notes
    summaryUrl: bootstrap.summaryUrl,
    detailUrl: bootstrap.detailUrl,
    addUrl: bootstrap.addUrl,
    csrfToken: bootstrap.csrfToken,
    nextUrl: bootstrap.nextUrl,
    canView: bootstrap.canView,
    canWrite: bootstrap.canWrite,
    canVote: false, // No voting on profile aggregate notes
    initialOpen: !bootstrap.compact,
  });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  document.querySelectorAll<HTMLElement>("[data-membership-notes-aggregate-root]").forEach((root) => {
    mountMembershipProfileNotes(root);
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}
