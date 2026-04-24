import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import OrganizationFormController from "../organization-form/OrganizationFormController.vue";

export function mountOrganizationForm(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }

  const mountTarget = document.createElement("div");
  root.appendChild(mountTarget);

  const app = createApp(OrganizationFormController);
  app.mount(mountTarget);
  return app;
}

function mountFromDocument(): void {
  const root = document.querySelector<HTMLElement>("[data-organization-form-root]");
  mountOrganizationForm(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}
