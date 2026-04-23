import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import MembershipAuditLogPage from "../membership-audit-log/MembershipAuditLogPage.vue";
import {
  type MembershipAuditLogBootstrap,
  readMembershipAuditLogBootstrap,
} from "../membership-audit-log/types";

export function mountMembershipAuditLogPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readMembershipAuditLogBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(MembershipAuditLogPage, {
    bootstrap,
  } satisfies { bootstrap: MembershipAuditLogBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-membership-audit-log-root]");
  mountMembershipAuditLogPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}
