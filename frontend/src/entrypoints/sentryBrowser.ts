import "vite/modulepreload-polyfill";

import * as SentryBrowser from "@sentry/browser";

import { attachSentryFeedbackTrigger } from "../shared/sentryFeedback";

declare global {
  interface Window {
    Sentry?: typeof SentryBrowser;
  }
}

window.Sentry = SentryBrowser;

function bindGlobalFooterFeedback(): void {
  if (document.querySelector('meta[name="sentry-capture-disabled"][content="true"]') !== null) {
    return;
  }

  attachSentryFeedbackTrigger(document.body, {
    allowScreenshot: true,
    surface: "global-footer",
  });
}

if (document.readyState === "complete") {
  bindGlobalFooterFeedback();
} else {
  document.addEventListener("DOMContentLoaded", () => {
    bindGlobalFooterFeedback();
  }, { once: true });
}

export {};