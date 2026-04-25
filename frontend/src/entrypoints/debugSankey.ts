import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import DebugSankeyPage from "../debug-sankey/DebugSankeyPage.vue";
import { readDebugSankeyBootstrap, type DebugSankeyBootstrap } from "../debug-sankey/types";

export function mountDebugSankeyPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readDebugSankeyBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(DebugSankeyPage, {
    bootstrap,
  } satisfies { bootstrap: DebugSankeyBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  mountDebugSankeyPage(document.querySelector<HTMLElement>("[data-debug-sankey-root]"));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}