import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import GroupDetailPage from "../group-detail/GroupDetailPage.vue";
import { readGroupDetailBootstrap, type GroupDetailBootstrap } from "../group-detail/types";
import { attachSentryFeedbackTrigger } from "../shared/sentryFeedback";

export function mountGroupDetailPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readGroupDetailBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(GroupDetailPage, {
    bootstrap,
  } satisfies { bootstrap: GroupDetailBootstrap });
  app.mount(root);
  attachSentryFeedbackTrigger(root, { allowScreenshot: true, surface: "group-detail" });
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-group-detail-root]");
  mountGroupDetailPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}
