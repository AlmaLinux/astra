import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import MembershipMinutesPage from "../membership-minutes/MembershipMinutesPage.vue";
import { type MembershipMinutesBootstrap, readMembershipMinutesBootstrap } from "../membership-minutes/types";

export function mountMembershipMinutesPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readMembershipMinutesBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(MembershipMinutesPage, {
    bootstrap,
  } satisfies { bootstrap: MembershipMinutesBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-membership-minutes-root]");
  mountMembershipMinutesPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}
