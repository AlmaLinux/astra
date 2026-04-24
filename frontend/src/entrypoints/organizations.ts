import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import OrganizationsPage from "../organizations/OrganizationsPage.vue";
import { readOrganizationsBootstrap, type OrganizationsBootstrap } from "../organizations/types";

export function mountOrganizationsPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readOrganizationsBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(OrganizationsPage, {
    bootstrap,
  } satisfies { bootstrap: OrganizationsBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-organizations-root]");
  mountOrganizationsPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}
