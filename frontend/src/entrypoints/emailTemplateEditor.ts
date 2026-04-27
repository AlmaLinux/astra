import { mountEmailTemplateEditorPage } from "./emailTools";

function mountFromDocument(): void {
  mountEmailTemplateEditorPage(document.querySelector<HTMLElement>("[data-email-template-editor-root]"));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}