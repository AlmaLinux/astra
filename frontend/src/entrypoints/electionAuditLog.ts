import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import ElectionAuditLogPage from "../election-audit-log/ElectionAuditLogPage.vue";
import { readElectionAuditBootstrap, type ElectionAuditBootstrap } from "../election-audit-log/types";

export function mountElectionAuditLogPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readElectionAuditBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(ElectionAuditLogPage, {
    bootstrap,
  } satisfies { bootstrap: ElectionAuditBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-election-audit-log-root]");
  mountElectionAuditLogPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}