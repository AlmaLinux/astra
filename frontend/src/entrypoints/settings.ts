import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import SettingsPage from "../settings/SettingsPage.vue";
import { readSettingsBootstrap } from "../settings/types";

function mountIntoRoot(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readSettingsBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  root.innerHTML = "";
  const mountPoint = document.createElement("div");
  mountPoint.setAttribute("data-settings-vue-root", "");
  root.appendChild(mountPoint);

  const app = createApp(SettingsPage, { bootstrap });
  app.mount(mountPoint);
  return app;
}

export function mountSettingsPage(root: HTMLElement | null): App<Element> | null {
  return mountIntoRoot(root);
}

function mountFromDocument(): void {
  mountIntoRoot(document.querySelector<HTMLElement>("[data-settings-root]"));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}