import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import RegistrationActivatePage from "../registration/RegistrationActivatePage.vue";
import RegistrationConfirmPage from "../registration/RegistrationConfirmPage.vue";
import RegistrationPage from "../registration/RegistrationPage.vue";
import { readRegisterActivateBootstrap, readRegisterConfirmBootstrap, readRegisterPageBootstrap } from "../registration/types";

export function mountRegisterPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readRegisterPageBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  root.innerHTML = "";
  const mountPoint = document.createElement("div");
  mountPoint.setAttribute("data-register-vue-root", "");
  root.appendChild(mountPoint);

  const app = createApp(RegistrationPage, { bootstrap });
  app.mount(mountPoint);
  return app;
}

export function mountRegisterConfirmPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readRegisterConfirmBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  root.innerHTML = "";
  const mountPoint = document.createElement("div");
  mountPoint.setAttribute("data-register-confirm-vue-root", "");
  root.appendChild(mountPoint);

  const app = createApp(RegistrationConfirmPage, { bootstrap });
  app.mount(mountPoint);
  return app;
}

export function mountRegisterActivatePage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readRegisterActivateBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  root.innerHTML = "";
  const mountPoint = document.createElement("div");
  mountPoint.setAttribute("data-register-activate-vue-root", "");
  root.appendChild(mountPoint);

  const app = createApp(RegistrationActivatePage, { bootstrap });
  app.mount(mountPoint);
  return app;
}

function mountFromDocument(): void {
  mountRegisterPage(document.querySelector<HTMLElement>("[data-register-root]"));
  mountRegisterConfirmPage(document.querySelector<HTMLElement>("[data-register-confirm-root]"));
  mountRegisterActivatePage(document.querySelector<HTMLElement>("[data-register-activate-root]"));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}