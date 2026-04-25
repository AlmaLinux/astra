import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import ElectionVotePage from "../election-vote/ElectionVotePage.vue";
import { readElectionVoteBootstrap, type ElectionVoteBootstrap } from "../election-vote/types";

export function mountElectionVotePage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readElectionVoteBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(ElectionVotePage, {
    bootstrap,
  } satisfies { bootstrap: ElectionVoteBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-election-vote-root]");
  mountElectionVotePage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}