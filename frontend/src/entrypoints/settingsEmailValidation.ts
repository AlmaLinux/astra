import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import SettingsEmailValidationPage from "../settings/SettingsEmailValidationPage.vue";
import { readSettingsEmailValidationBootstrap } from "../settings/types";

function mountIntoRoot(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readSettingsEmailValidationBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  root.innerHTML = "";
  const mountPoint = document.createElement("div");
  mountPoint.setAttribute("data-settings-email-validation-vue-root", "");
  root.appendChild(mountPoint);

  const app = createApp(SettingsEmailValidationPage, { bootstrap });
  app.mount(mountPoint);
  return app;
}

export function mountSettingsEmailValidationPage(root: HTMLElement | null): App<Element> | null {
  return mountIntoRoot(root);
}

function mountFromDocument(): void {
  mountIntoRoot(document.querySelector<HTMLElement>("[data-settings-email-validation-root]"));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}