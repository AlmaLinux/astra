import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import MembershipRequestFormPage from "../membership-request-form/MembershipRequestFormPage.vue";
import { readMembershipRequestFormBootstrap } from "../membership-request-form/types";

export function mountMembershipRequestFormPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readMembershipRequestFormBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  root.innerHTML = "";
  const mountPoint = document.createElement("div");
  root.appendChild(mountPoint);

  const app = createApp(MembershipRequestFormPage, { bootstrap });
  app.mount(mountPoint);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-membership-request-form-root]");
  mountMembershipRequestFormPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}