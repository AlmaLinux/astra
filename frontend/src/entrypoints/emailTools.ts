import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import EmailTemplateEditorPage from "../email-tools/EmailTemplateEditorPage.vue";
import EmailTemplatesPage from "../email-tools/EmailTemplatesPage.vue";
import MailImagesPage from "../email-tools/MailImagesPage.vue";
import SendMailPage from "../email-tools/SendMailPage.vue";
import { readEmailTemplateEditorBootstrap, readEmailTemplatesBootstrap, readMailImagesBootstrap, readSendMailBootstrap } from "../email-tools/types";

export function mountEmailTemplatesPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readEmailTemplatesBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  root.innerHTML = "";
  const mountPoint = document.createElement("div");
  mountPoint.setAttribute("data-email-templates-vue-root", "");
  root.appendChild(mountPoint);

  const app = createApp(EmailTemplatesPage, { bootstrap });
  app.mount(mountPoint);
  return app;
}

export function mountEmailTemplateEditorPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readEmailTemplateEditorBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  root.innerHTML = "";
  const mountPoint = document.createElement("div");
  mountPoint.setAttribute("data-email-template-editor-vue-root", "");
  root.appendChild(mountPoint);

  const app = createApp(EmailTemplateEditorPage, { bootstrap });
  app.mount(mountPoint);
  return app;
}

export function mountMailImagesPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readMailImagesBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  root.innerHTML = "";
  const mountPoint = document.createElement("div");
  mountPoint.setAttribute("data-mail-images-vue-root", "");
  root.appendChild(mountPoint);

  const app = createApp(MailImagesPage, { bootstrap });
  app.mount(mountPoint);
  return app;
}

export function mountSendMailPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readSendMailBootstrap(root);
  if (bootstrap === null) {
    return null;
  }

  root.innerHTML = "";
  const mountPoint = document.createElement("div");
  mountPoint.setAttribute("data-send-mail-vue-root", "");
  root.appendChild(mountPoint);

  const app = createApp(SendMailPage, { bootstrap });
  app.mount(mountPoint);
  return app;
}