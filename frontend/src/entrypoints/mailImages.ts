import { mountMailImagesPage } from "./emailTools";

function mountFromDocument(): void {
  mountMailImagesPage(document.querySelector<HTMLElement>("[data-mail-images-root]"));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}