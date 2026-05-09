import "vite/modulepreload-polyfill";

import * as SentryBrowser from "@sentry/browser";

import { attachSentryFeedbackTrigger } from "../shared/sentryFeedback";

declare global {
  interface Window {
    Sentry?: typeof SentryBrowser;
    __astraSentryBrowserState?: {
      footerFeedbackBound: boolean;
      readyListenerInstalled: boolean;
    };
  }
}

window.Sentry = SentryBrowser;

const browserState = window.__astraSentryBrowserState ?? {
  footerFeedbackBound: false,
  readyListenerInstalled: false,
};
window.__astraSentryBrowserState = browserState;

function bindGlobalFooterFeedback(): void {
  if (browserState.footerFeedbackBound) {
    return;
  }

  browserState.footerFeedbackBound = attachSentryFeedbackTrigger(document.body, {
    allowScreenshot: true,
    surface: "global-footer",
  });
}

if (!browserState.readyListenerInstalled) {
  document.addEventListener("astra:sentry-ready", () => {
    bindGlobalFooterFeedback();
  });
  browserState.readyListenerInstalled = true;
}

if (document.readyState !== "loading") {
  bindGlobalFooterFeedback();
} else {
  document.addEventListener("DOMContentLoaded", () => {
    bindGlobalFooterFeedback();
  }, { once: true });
}

export {};