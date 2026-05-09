import "vite/modulepreload-polyfill";

import * as SentryBrowser from "@sentry/browser";

declare global {
  interface Window {
    Sentry?: typeof SentryBrowser;
  }
}

window.Sentry = SentryBrowser;

export {};