import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import MembershipRequestsPage from "../membership-requests/MembershipRequestsPage.vue";
import {
  type MembershipRequestsBootstrap,
  readMembershipRequestsBootstrap,
} from "../membership-requests/types";

export function mountMembershipRequestsPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readMembershipRequestsBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(MembershipRequestsPage, {
    bootstrap,
  } satisfies { bootstrap: MembershipRequestsBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-membership-requests-root]");
  mountMembershipRequestsPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}