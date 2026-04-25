import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import ElectionsPage from "../elections/ElectionsPage.vue";
import { readElectionsBootstrap, type ElectionsBootstrap } from "../elections/types";

export function mountElectionsPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readElectionsBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(ElectionsPage, {
    bootstrap,
  } satisfies { bootstrap: ElectionsBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-elections-root]");
  mountElectionsPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}