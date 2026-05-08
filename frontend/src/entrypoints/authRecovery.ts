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

export function mountPasswordResetRequestPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readPasswordResetRequestBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  root.innerHTML = "";
  const mountPoint = document.createElement("div");
  mountPoint.setAttribute("data-auth-recovery-password-reset-vue-root", "");
  root.appendChild(mountPoint);

  const app = createApp(PasswordResetRequestPage, { bootstrap });
  app.mount(mountPoint);
  return app;
}

export function mountPasswordResetConfirmPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readPasswordResetConfirmBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  root.innerHTML = "";
  const mountPoint = document.createElement("div");
  mountPoint.setAttribute("data-auth-recovery-password-reset-confirm-vue-root", "");
  root.appendChild(mountPoint);

  const app = createApp(PasswordResetConfirmPage, { bootstrap });
  app.mount(mountPoint);
  return app;
}

export function mountPasswordExpiredPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readPasswordExpiredBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  root.innerHTML = "";
  const mountPoint = document.createElement("div");
  mountPoint.setAttribute("data-auth-recovery-password-expired-vue-root", "");
  root.appendChild(mountPoint);

  const app = createApp(PasswordExpiredPage, { bootstrap });
  app.mount(mountPoint);
  return app;
}

export function mountOtpSyncPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readOtpSyncBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  root.innerHTML = "";
  const mountPoint = document.createElement("div");
  mountPoint.setAttribute("data-auth-recovery-otp-sync-vue-root", "");
  root.appendChild(mountPoint);

  const app = createApp(OtpSyncPage, { bootstrap });
  app.mount(mountPoint);
  return app;
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