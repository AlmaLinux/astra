import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import ElectionAlgorithmPage from "../election-algorithm/ElectionAlgorithmPage.vue";
import { readElectionAlgorithmBootstrap, type ElectionAlgorithmBootstrap } from "../election-algorithm/types";

export function mountElectionAlgorithmPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readElectionAlgorithmBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(ElectionAlgorithmPage, {
    bootstrap,
  } satisfies { bootstrap: ElectionAlgorithmBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-election-algorithm-root]");
  mountElectionAlgorithmPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}