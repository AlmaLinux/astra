import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import SendMailPage from "../SendMailPage.vue";
import type { SendMailBootstrap } from "../types";

const bootstrap: SendMailBootstrap = {
  apiUrl: "/api/v1/email-tools/send-mail/detail",
  submitUrl: "/email-tools/send-mail/",
  previewUrl: "/email-tools/send-mail/render-preview/",
  csrfToken: "csrf-token",
  initialPayload: {
    selectedRecipientMode: "manual",
    actionStatus: "approved",
    actionNotice: "",
    hasSavedCsvRecipients: false,
    createdTemplateId: null,
    templates: [],
    form: {
      isBound: true,
      nonFieldErrors: [],
      fields: [
        {
          name: "recipient_mode",
          id: "send-mail-recipient-mode",
          widget: "hidden",
          value: "manual",
          required: false,
          disabled: false,
          errors: [],
          attrs: {},
        },
        {
          name: "manual_to",
          id: "id_manual_to",
          widget: "text",
          value: "jim@example.com, bob@example.com",
          required: false,
          disabled: false,
          errors: [],
          attrs: { class: "form-control", placeholder: "clara@example.com, alex@example.com" },
        },
        {
          name: "cc",
          id: "id_cc",
          widget: "text",
          value: "",
          required: false,
          disabled: false,
          errors: [],
          attrs: { class: "form-control" },
        },
        {
          name: "bcc",
          id: "id_bcc",
          widget: "text",
          value: "",
          required: false,
          disabled: false,
          errors: [],
          attrs: { class: "form-control" },
        },
        {
          name: "reply_to",
          id: "id_reply_to",
          widget: "text",
          value: "",
          required: false,
          disabled: false,
          errors: [],
          attrs: { class: "form-control" },
        },
        {
          name: "subject",
          id: "id_subject",
          widget: "text",
          value: "Hello {{ email }}",
          required: false,
          disabled: false,
          errors: [],
          attrs: { class: "form-control" },
        },
        {
          name: "html_content",
          id: "id_html_content",
          widget: "textarea",
          value: "<p>Hello {{ email }}</p>",
          required: false,
          disabled: false,
          errors: [],
          attrs: { class: "form-control", rows: "12", spellcheck: "true" },
        },
        {
          name: "text_content",
          id: "id_text_content",
          widget: "textarea",
          value: "Hello {{ email }}",
          required: false,
          disabled: false,
          errors: [],
          attrs: { class: "form-control", rows: "12", spellcheck: "true" },
        },
      ],
    },
    recipientPreview: {
      variables: [{ name: "email", example: "jim@example.com" }],
      recipientCount: 2,
      firstContext: { email: "jim@example.com" },
      skippedCount: 0,
    },
    compose: {
      selectedTemplateId: null,
      preview: {
        subject: "Hello jim@example.com",
        html: "<p>Hello jim@example.com</p>",
        text: "Hello jim@example.com",
      },
    },
  },
};

describe("SendMailPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    delete (window as Window & { SendMailPage?: unknown }).SendMailPage;
    delete (window as Window & { TemplatedEmailComposeRegistry?: unknown }).TemplatedEmailComposeRegistry;
  });

  it("renders the send-mail shell from the data-only payload and marks preview bootstrap as authoritative", async () => {
    const initAll = vi.fn();
    const initPage = vi.fn();
    (window as Window & { TemplatedEmailComposeRegistry?: { initAll: typeof initAll } }).TemplatedEmailComposeRegistry = { initAll };
    (window as Window & { SendMailPage?: { init: typeof initPage } }).SendMailPage = { init: initPage };

    const wrapper = mount(SendMailPage, {
      props: { bootstrap },
      attachTo: document.body,
    });

    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(wrapper.find('form[action="/email-tools/send-mail/"]').exists()).toBe(true);
    expect(wrapper.get("#send-mail-recipient-mode").element).toHaveProperty("value", "manual");
    expect(wrapper.text()).toContain("Recipient count");
    expect(wrapper.text()).toContain("2");
    expect(wrapper.find("[data-templated-email-compose]").attributes("data-compose-skip-initial-preview-refresh")).toBe("1");
    expect(wrapper.find('iframe[data-compose-preview-iframe="1"]').exists()).toBe(true);
    expect(initAll).toHaveBeenCalled();
    expect(initPage).toHaveBeenCalled();
  });
});