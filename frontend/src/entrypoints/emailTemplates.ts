import { mountEmailTemplatesPage } from "./emailTools";

function mountFromDocument(): void {
  mountEmailTemplatesPage(document.querySelector<HTMLElement>("[data-email-templates-root]"));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}