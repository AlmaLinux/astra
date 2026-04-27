import "vite/modulepreload-polyfill";

import { createApp, type App } from "vue";

import EmailTemplateEditorPage from "../email-tools/EmailTemplateEditorPage.vue";
import EmailTemplatesPage from "../email-tools/EmailTemplatesPage.vue";
import MailImagesPage from "../email-tools/MailImagesPage.vue";
import SendMailPage from "../email-tools/SendMailPage.vue";
import { readEmailTemplateEditorBootstrap, readEmailTemplatesBootstrap, readMailImagesBootstrap, readSendMailBootstrap } from "../email-tools/types";

function mountIntoRoot<T>(root: HTMLElement | null, component: T, props: object, vueRootAttr: string): App<Element> | null {
  if (root === null) {
    return null;
  }

  root.innerHTML = "";
  const mountPoint = document.createElement("div");
  mountPoint.setAttribute(vueRootAttr, "");
  root.appendChild(mountPoint);

  const app = createApp(component as never, props);
  app.mount(mountPoint);
  return app;
}

export function mountEmailTemplatesPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readEmailTemplatesBootstrap(root);
  if (bootstrap === null) {
    return null;
  }
  return mountIntoRoot(root, EmailTemplatesPage, { bootstrap }, "data-email-templates-vue-root");
}

export function mountEmailTemplateEditorPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readEmailTemplateEditorBootstrap(root);
  if (bootstrap === null) {
    return null;
  }
  return mountIntoRoot(root, EmailTemplateEditorPage, { bootstrap }, "data-email-template-editor-vue-root");
}

export function mountMailImagesPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readMailImagesBootstrap(root);
  if (bootstrap === null) {
    return null;
  }
  return mountIntoRoot(root, MailImagesPage, { bootstrap }, "data-mail-images-vue-root");
}

export function mountSendMailPage(root: HTMLElement | null): App<Element> | null {
  if (root === null) {
    return null;
  }
  const bootstrap = readSendMailBootstrap(root);
  if (bootstrap === null) {
    return null;
  }
  return mountIntoRoot(root, SendMailPage, { bootstrap }, "data-send-mail-vue-root");
}