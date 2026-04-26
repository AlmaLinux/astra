import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import MembershipRequestDetailPage from "../membership-request-detail/MembershipRequestDetailPage.vue";
import { readMembershipRequestDetailBootstrap } from "../membership-request-detail/types";

export function mountMembershipRequestDetailPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readMembershipRequestDetailBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  root.innerHTML = "";
  const mountPoint = document.createElement("div");
  root.appendChild(mountPoint);

  const app = createApp(MembershipRequestDetailPage, {
    bootstrap,
  });
  app.mount(mountPoint);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-membership-request-detail-root]");
  mountMembershipRequestDetailPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}
