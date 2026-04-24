import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import GroupFormPage from "../group-form/GroupFormPage.vue";
import { readGroupFormBootstrap, type GroupFormBootstrap } from "../group-form/types";

export function mountGroupFormPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readGroupFormBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(GroupFormPage, {
    bootstrap,
  } satisfies { bootstrap: GroupFormBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-group-form-root]");
  mountGroupFormPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}
