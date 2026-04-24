import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import OrganizationClaimPage from "../organization-claim/OrganizationClaimPage.vue";
import { readOrganizationClaimBootstrap, type OrganizationClaimBootstrap } from "../organization-claim/types";

export function mountOrganizationClaimPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readOrganizationClaimBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(OrganizationClaimPage, {
    bootstrap,
  } satisfies { bootstrap: OrganizationClaimBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-organization-claim-root]");
  mountOrganizationClaimPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}