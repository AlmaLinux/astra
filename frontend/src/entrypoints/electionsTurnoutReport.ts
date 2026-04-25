import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import ElectionsTurnoutReportPage from "../elections-turnout-report/ElectionsTurnoutReportPage.vue";
import {
  readElectionsTurnoutReportBootstrap,
  type ElectionsTurnoutReportBootstrap,
} from "../elections-turnout-report/types";

export function mountElectionsTurnoutReportPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readElectionsTurnoutReportBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(ElectionsTurnoutReportPage, {
    bootstrap,
  } satisfies { bootstrap: ElectionsTurnoutReportBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-elections-turnout-report-root]");
  mountElectionsTurnoutReportPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}