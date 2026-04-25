import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import BallotVerifyPage from "../ballot-verify/BallotVerifyPage.vue";
import { readBallotVerifyBootstrap, type BallotVerifyBootstrap } from "../ballot-verify/types";

export function mountBallotVerifyPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readBallotVerifyBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(BallotVerifyPage, {
    bootstrap,
  } satisfies { bootstrap: BallotVerifyBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-ballot-verify-root]");
  mountBallotVerifyPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}