import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import MembershipRequestDetailActions from "../membership-requests/components/MembershipRequestDetailActions.vue";
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

export interface MembershipRequestDetailActionsBootstrap {
  requestId: number;
  requestStatus: string;
  membershipTypeName: string;
  requestTarget: string;
  approveUrl: string;
  approveOnHoldUrl: string;
  rejectUrl: string;
  rfiUrl: string;
  ignoreUrl: string;
  canRequestInfo: boolean;
  showOnHoldApprove: boolean;
  csrfToken: string;
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

function readMembershipRequestDetailActionsBootstrap(
  root: HTMLElement,
): MembershipRequestDetailActionsBootstrap | null {
  const requestIdStr = root.getAttribute("data-membership-request-id");
  const requestStatus = root.getAttribute("data-membership-request-status");
  const membershipTypeName = root.getAttribute("data-membership-request-membership-type-name");
  const requestTarget = root.getAttribute("data-membership-request-target");
  const approveUrl = root.getAttribute("data-membership-request-api-approve-url");
  const approveOnHoldUrl = root.getAttribute("data-membership-request-api-approve-on-hold-url");
  const rejectUrl = root.getAttribute("data-membership-request-api-reject-url");
  const rfiUrl = root.getAttribute("data-membership-request-api-rfi-url");
  const ignoreUrl = root.getAttribute("data-membership-request-api-ignore-url");
  const canRequestInfo = root.getAttribute("data-membership-request-can-request-info");
  const showOnHoldApprove = root.getAttribute("data-membership-request-show-on-hold-approve");
  const csrfToken = root.getAttribute("data-membership-request-csrf-token");

  if (
    !requestIdStr
    || !requestStatus
    || !membershipTypeName
    || !requestTarget
    || !approveUrl
    || !approveOnHoldUrl
    || !rejectUrl
    || !rfiUrl
    || !ignoreUrl
    || canRequestInfo === null
    || showOnHoldApprove === null
    || csrfToken === null
  ) {
    return null;
  }

  const requestId = parseInt(requestIdStr, 10);
  if (isNaN(requestId)) {
    return null;
  }

  return {
    requestId,
    requestStatus,
    membershipTypeName,
    requestTarget,
    approveUrl,
    approveOnHoldUrl,
    rejectUrl,
    rfiUrl,
    ignoreUrl,
    canRequestInfo: canRequestInfo === "true",
    showOnHoldApprove: showOnHoldApprove === "true",
    csrfToken,
  };
}

export function mountMembershipRequestDetailActions(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readMembershipRequestDetailActionsBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(MembershipRequestDetailActions, {
    ...bootstrap,
    onActionSuccess: () => {
      window.location.reload();
    },
  });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const notesRoot = document.querySelector<HTMLElement>("[data-membership-request-notes-root]");
  mountMembershipRequestDetail(notesRoot);

  const actionsRoot = document.querySelector<HTMLElement>("[data-membership-request-actions-root]");
  mountMembershipRequestDetailActions(actionsRoot);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}
