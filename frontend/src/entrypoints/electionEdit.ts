import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import ElectionEditController from "../election-edit/ElectionEditController.vue";

export function mountElectionEditController(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const app = createApp(ElectionEditController);
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  mountElectionEditController(document.querySelector<HTMLElement>("[data-election-edit-root]"));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}
