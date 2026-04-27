import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import OtpSyncPage from "../auth-recovery/OtpSyncPage.vue";
import PasswordExpiredPage from "../auth-recovery/PasswordExpiredPage.vue";
import PasswordResetConfirmPage from "../auth-recovery/PasswordResetConfirmPage.vue";
import PasswordResetRequestPage from "../auth-recovery/PasswordResetRequestPage.vue";
import {
  readOtpSyncBootstrap,
  readPasswordExpiredBootstrap,
  readPasswordResetConfirmBootstrap,
  readPasswordResetRequestBootstrap,
} from "../auth-recovery/types";

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

export function mountPasswordResetRequestPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readPasswordResetRequestBootstrap(root);
  if (bootstrap === null) {
    return null;
  }
  return mountIntoRoot(root, PasswordResetRequestPage, { bootstrap }, "data-auth-recovery-password-reset-vue-root");
}

export function mountPasswordResetConfirmPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readPasswordResetConfirmBootstrap(root);
  if (bootstrap === null) {
    return null;
  }
  return mountIntoRoot(root, PasswordResetConfirmPage, { bootstrap }, "data-auth-recovery-password-reset-confirm-vue-root");
}

export function mountPasswordExpiredPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readPasswordExpiredBootstrap(root);
  if (bootstrap === null) {
    return null;
  }
  return mountIntoRoot(root, PasswordExpiredPage, { bootstrap }, "data-auth-recovery-password-expired-vue-root");
}

export function mountOtpSyncPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readOtpSyncBootstrap(root);
  if (bootstrap === null) {
    return null;
  }
  return mountIntoRoot(root, OtpSyncPage, { bootstrap }, "data-auth-recovery-otp-sync-vue-root");
}

function mountFromDocument(): void {
  mountPasswordResetRequestPage(document.querySelector<HTMLElement>("[data-auth-recovery-password-reset-root]"));
  mountPasswordResetConfirmPage(document.querySelector<HTMLElement>("[data-auth-recovery-password-reset-confirm-root]"));
  mountPasswordExpiredPage(document.querySelector<HTMLElement>("[data-auth-recovery-password-expired-root]"));
  mountOtpSyncPage(document.querySelector<HTMLElement>("[data-auth-recovery-otp-sync-root]"));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}