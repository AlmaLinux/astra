import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import UserProfilePage from "../user-profile/UserProfilePage.vue";
import { readUserProfileBootstrap, type UserProfileBootstrap } from "../user-profile/types";

export function mountUserProfilePage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readUserProfileBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(UserProfilePage, {
    bootstrap,
  } satisfies { bootstrap: UserProfileBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-user-profile-root]");
  mountUserProfilePage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}