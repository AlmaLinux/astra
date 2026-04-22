import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import MembershipNotesCard from "../membership-requests/components/MembershipNotesCard.vue";

export interface MembershipRequestDetailBootstrap {
  requestId: number;
  summaryUrl: string;
  detailUrl: string;
  addUrl: string;
  csrfToken: string;
  nextUrl: string;
  canView: boolean;
  canWrite: boolean;
  canVote: boolean;
  initialOpen: boolean;
}

function readMembershipRequestDetailBootstrap(root: HTMLElement): MembershipRequestDetailBootstrap | null {
  const requestIdStr = root.getAttribute("data-membership-request-id");
  const summaryUrl = root.getAttribute("data-membership-request-notes-summary-url");
  const detailUrl = root.getAttribute("data-membership-request-notes-detail-url");
  const addUrl = root.getAttribute("data-membership-request-notes-add-url");
  const csrfToken = root.getAttribute("data-csrf-token");
  const nextUrl = root.getAttribute("data-next-url");
  const canViewStr = root.getAttribute("data-can-view");
  const canWriteStr = root.getAttribute("data-can-write");
  const canVoteStr = root.getAttribute("data-can-vote");

  if (
    !requestIdStr ||
    !summaryUrl ||
    !detailUrl ||
    !addUrl ||
    !csrfToken ||
    canViewStr === null ||
    canWriteStr === null ||
    canVoteStr === null
  ) {
    return null;
  }

  const requestId = parseInt(requestIdStr, 10);
  if (isNaN(requestId)) {
    return null;
  }

  return {
    requestId,
    summaryUrl,
    detailUrl,
    addUrl,
    csrfToken,
    nextUrl: nextUrl || "",
    canView: canViewStr === "true",
    canWrite: canWriteStr === "true",
    canVote: canVoteStr === "true",
    initialOpen: true,
  };
}

export function mountMembershipRequestDetail(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readMembershipRequestDetailBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(MembershipNotesCard, {
    requestId: bootstrap.requestId,
    summaryUrl: bootstrap.summaryUrl,
    detailUrl: bootstrap.detailUrl,
    addUrl: bootstrap.addUrl,
    csrfToken: bootstrap.csrfToken,
    nextUrl: bootstrap.nextUrl,
    canView: bootstrap.canView,
    canWrite: bootstrap.canWrite,
    canVote: bootstrap.canVote,
    initialOpen: bootstrap.initialOpen,
  });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-membership-request-notes-root]");
  mountMembershipRequestDetail(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}
