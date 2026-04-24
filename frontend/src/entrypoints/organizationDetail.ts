import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import OrganizationDetailPage from "../organization-detail/OrganizationDetailPage.vue";
import { readOrganizationDetailBootstrap, type OrganizationDetailBootstrap } from "../organization-detail/types";

export function mountOrganizationDetailPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readOrganizationDetailBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(OrganizationDetailPage, {
    bootstrap,
  } satisfies { bootstrap: OrganizationDetailBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-organization-detail-root]");
  mountOrganizationDetailPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}
