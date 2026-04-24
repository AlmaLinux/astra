import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import UserProfileController from "../user-profile/UserProfileController.vue";
import UserProfileGroupsPanel from "../user-profile/UserProfileGroupsPanel.vue";
import UserProfileSummary from "../user-profile/UserProfileSummary.vue";
import {
  readUserProfileGroupsBootstrap,
  readUserProfileSummaryBootstrap,
  type UserProfileGroupsBootstrap,
  type UserProfileSummaryBootstrap,
} from "../user-profile/types";

export function mountUserProfileController(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const mountTarget = document.createElement("div");
  root.appendChild(mountTarget);

  const app = createApp(UserProfileController);
  app.mount(mountTarget);
  return app;
}

export function mountUserProfileSummary(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readUserProfileSummaryBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(UserProfileSummary, {
    bootstrap,
  } satisfies { bootstrap: UserProfileSummaryBootstrap });
  app.mount(root);
  return app;
}

export function mountUserProfileGroupsPanel(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readUserProfileGroupsBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(UserProfileGroupsPanel, {
    bootstrap,
  } satisfies { bootstrap: UserProfileGroupsBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const summaryRoot = document.querySelector<HTMLElement>("[data-user-profile-summary-root]");
  mountUserProfileSummary(summaryRoot);

  const groupsRoot = document.querySelector<HTMLElement>("[data-user-profile-groups-root]");
  mountUserProfileGroupsPanel(groupsRoot);

  const root = document.querySelector<HTMLElement>("[data-user-profile-root]");
  mountUserProfileController(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}