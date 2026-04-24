import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import GroupsPage from "../groups/GroupsPage.vue";
import { readGroupsBootstrap, type GroupsBootstrap } from "../groups/types";

export function mountGroupsPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readGroupsBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(GroupsPage, {
    bootstrap,
  } satisfies { bootstrap: GroupsBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-groups-root]");
  mountGroupsPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}
