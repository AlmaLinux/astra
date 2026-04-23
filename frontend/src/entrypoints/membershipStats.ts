import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import MembershipStatsPage from "../membership-stats/MembershipStatsPage.vue";
import { type MembershipStatsBootstrap, readMembershipStatsBootstrap } from "../membership-stats/types";

export function mountMembershipStatsPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readMembershipStatsBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(MembershipStatsPage, {
    bootstrap,
  } satisfies { bootstrap: MembershipStatsBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-membership-stats-root]");
  mountMembershipStatsPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}
