import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import MembershipSponsorsPage from "../membership-sponsors/MembershipSponsorsPage.vue";
import {
  type MembershipSponsorsBootstrap,
  readMembershipSponsorsBootstrap,
} from "../membership-sponsors/types";

export function mountMembershipSponsorsPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const bootstrap = readMembershipSponsorsBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  const app = createApp(MembershipSponsorsPage, {
    bootstrap,
  } satisfies { bootstrap: MembershipSponsorsBootstrap });
  app.mount(root);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-membership-sponsors-root]");
  mountMembershipSponsorsPage(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}
