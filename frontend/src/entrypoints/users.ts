import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import UsersPage from "../users/UsersPage.vue";
import { readUsersBootstrap, type UsersBootstrap } from "../users/types";

export function mountUsersPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readUsersBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(UsersPage, {
    bootstrap,
  } satisfies { bootstrap: UsersBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-users-root]");
  mountUsersPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}
