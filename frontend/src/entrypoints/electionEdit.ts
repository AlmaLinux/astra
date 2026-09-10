import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import ElectionEditController from "../election-edit/ElectionEditController.vue";
import ElectionStartAutomation from "../election-detail/ElectionStartAutomation.vue";
import {
  readElectionStartAutomationBootstrap,
  type ElectionStartAutomationBootstrap,
} from "../election-detail/types";

export function mountElectionEditController(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const app = createApp(ElectionEditController);
  app.mount(root);
  return app;
}

export function mountElectionStartAutomation(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readElectionStartAutomationBootstrap(root);
  if (bootstrap === null) {
    return null;
  }
  const app = createApp(ElectionStartAutomation, {
    bootstrap,
  } satisfies { bootstrap: ElectionStartAutomationBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  mountElectionEditController(document.querySelector<HTMLElement>("[data-election-edit-root]"));
  mountElectionStartAutomation(document.querySelector<HTMLElement>("[data-election-start-automation-root]"));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}
