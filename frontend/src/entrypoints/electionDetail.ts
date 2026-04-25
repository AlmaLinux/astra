import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import EligibleVotersGrid from "../election-detail/EligibleVotersGrid.vue";
import ElectionActionCard from "../election-detail/ElectionActionCard.vue";
import ElectionConcludeAction from "../election-detail/ElectionConcludeAction.vue";
import ElectionCredentialResendControls from "../election-detail/ElectionCredentialResendControls.vue";
import ElectionExtendAction from "../election-detail/ElectionExtendAction.vue";
import IneligibleVoterModal from "../election-detail/IneligibleVoterModal.vue";
import ElectionDetailSummaryPage from "../election-detail/ElectionDetailSummaryPage.vue";
import ElectionVoterSearchForm from "../election-detail/ElectionVoterSearchForm.vue";
import {
  readElectionActionCardBootstrap,
  readElectionConcludeActionBootstrap,
  readElectionCredentialResendBootstrap,
  readElectionDetailBootstrap,
  readElectionExtendActionBootstrap,
  readEligibleVotersBootstrap,
  readElectionVoterSearchBootstrap,
  readIneligibleVoterModalBootstrap,
  type EligibleVotersBootstrap,
  type ElectionActionCardBootstrap,
  type ElectionConcludeActionBootstrap,
  type ElectionCredentialResendBootstrap,
  type ElectionDetailBootstrap,
  type ElectionExtendActionBootstrap,
  type ElectionVoterSearchBootstrap,
  type IneligibleVoterModalBootstrap,
} from "../election-detail/types";

export function mountElectionDetailPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readElectionDetailBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(ElectionDetailSummaryPage, {
    bootstrap,
  } satisfies { bootstrap: ElectionDetailBootstrap });
  app.mount(root);
  return app;
}

export function mountElectionExtendAction(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readElectionExtendActionBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(ElectionExtendAction, {
    bootstrap,
  } satisfies { bootstrap: ElectionExtendActionBootstrap });
  app.mount(root);
  return app;
}

export function mountElectionConcludeAction(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readElectionConcludeActionBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(ElectionConcludeAction, {
    bootstrap,
  } satisfies { bootstrap: ElectionConcludeActionBootstrap });
  app.mount(root);
  return app;
}

export function mountElectionCredentialResendControls(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readElectionCredentialResendBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(ElectionCredentialResendControls, {
    bootstrap,
  } satisfies { bootstrap: ElectionCredentialResendBootstrap });
  app.mount(root);
  return app;
}

export function mountElectionActionCard(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readElectionActionCardBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(ElectionActionCard, {
    bootstrap,
  } satisfies { bootstrap: ElectionActionCardBootstrap });
  app.mount(root);
  return app;
}

export function mountIneligibleVoterModal(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readIneligibleVoterModalBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(IneligibleVoterModal, {
    bootstrap,
  } satisfies { bootstrap: IneligibleVoterModalBootstrap });
  app.mount(root);
  return app;
}

export function mountEligibleVotersGrid(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readEligibleVotersBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(EligibleVotersGrid, {
    bootstrap,
  } satisfies { bootstrap: EligibleVotersBootstrap });
  app.mount(root);
  return app;
}

export function mountElectionVoterSearchForm(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readElectionVoterSearchBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(ElectionVoterSearchForm, {
    bootstrap,
  } satisfies { bootstrap: ElectionVoterSearchBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  mountElectionDetailPage(document.querySelector<HTMLElement>("[data-election-detail-root]"));
  mountElectionActionCard(document.querySelector<HTMLElement>("[data-election-detail-action-root]"));
  mountElectionExtendAction(document.querySelector<HTMLElement>("[data-election-extend-action-root]"));
  mountElectionConcludeAction(document.querySelector<HTMLElement>("[data-election-conclude-action-root]"));
  mountElectionCredentialResendControls(document.querySelector<HTMLElement>("[data-election-credential-resend-root]"));
  mountIneligibleVoterModal(document.querySelector<HTMLElement>("[data-ineligible-voter-modal-root]"));
  mountEligibleVotersGrid(document.querySelector<HTMLElement>("[data-election-eligible-voters-root]"));
  document.querySelectorAll<HTMLElement>("[data-election-voter-search-root]").forEach((root) => {
    mountElectionVoterSearchForm(root);
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}