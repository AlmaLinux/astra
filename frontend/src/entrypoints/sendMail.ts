import { mountSendMailPage } from "./emailTools";

function mountFromDocument(): void {
  mountSendMailPage(document.querySelector<HTMLElement>("[data-send-mail-root]"));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountFromDocument, { once: true });
} else {
  mountFromDocument();
}