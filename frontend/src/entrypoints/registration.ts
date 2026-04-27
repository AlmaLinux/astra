import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import RegistrationActivatePage from "../registration/RegistrationActivatePage.vue";
import RegistrationConfirmPage from "../registration/RegistrationConfirmPage.vue";
import RegistrationPage from "../registration/RegistrationPage.vue";
import { readRegisterActivateBootstrap, readRegisterConfirmBootstrap, readRegisterPageBootstrap } from "../registration/types";

function mountIntoRoot<T>(root: HTMLElement | null, component: T, props: object, vueRootAttr: string): App<Element> | null {
  if (root === null) {
    return null;
  }

  root.innerHTML = "";
  const mountPoint = document.createElement("div");
  mountPoint.setAttribute(vueRootAttr, "");
  root.appendChild(mountPoint);

  const app = createApp(component as never, props);
  app.mount(mountPoint);
  return app;
}

export function mountRegisterPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readRegisterPageBootstrap(root);
  if (bootstrap === null) {
    return null;
  }
  return mountIntoRoot(root, RegistrationPage, { bootstrap }, "data-register-vue-root");
}

export function mountRegisterConfirmPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readRegisterConfirmBootstrap(root);
  if (bootstrap === null) {
    return null;
  }
  return mountIntoRoot(root, RegistrationConfirmPage, { bootstrap }, "data-register-confirm-vue-root");
}

export function mountRegisterActivatePage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readRegisterActivateBootstrap(root);
  if (bootstrap === null) {
    return null;
  }
  return mountIntoRoot(root, RegistrationActivatePage, { bootstrap }, "data-register-activate-vue-root");
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