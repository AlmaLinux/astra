import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import EmailTemplateEditorPage from "../EmailTemplateEditorPage.vue";
import type { EmailTemplateEditorBootstrap } from "../types";

const bootstrap: EmailTemplateEditorBootstrap = {
  apiUrl: "/api/v1/email-tools/templates/7/detail",
  listUrl: "/email-tools/templates/",
  submitUrl: "/email-tools/templates/7/",
  previewUrl: "/email-tools/templates/render-preview/",
  deleteUrl: "/email-tools/templates/7/delete/",
  csrfToken: "csrf-token",
  initialPayload: {
    mode: "edit",
    template: {
      id: 7,
      name: "membership-request-rfi",
      description: "Locked",
      isLocked: true,
    },
    form: {
      isBound: true,
      nonFieldErrors: ["Cannot save right now."],
      fields: [
        {
          name: "name",
          id: "id_name",
          widget: "text",
          value: "membership-request-rfi",
          required: true,
          disabled: true,
          errors: ["This template is referenced by the app configuration and cannot be renamed. Update settings (or switch to a different template) first."],
          attrs: { class: "form-control", required: "required" },
        },
        {
          name: "description",
          id: "id_description",
          widget: "text",
          value: "Locked",
          required: false,
          disabled: false,
          errors: [],
          attrs: { class: "form-control" },
        },
        {
          name: "subject",
          id: "id_subject",
          widget: "text",
          value: "Hello {{ username }}",
          required: false,
          disabled: false,
          errors: [],
          attrs: { class: "form-control" },
        },
        {
          name: "html_content",
          id: "id_html_content",
          widget: "textarea",
          value: "<p>Hello {{ username }}</p>",
          required: false,
          disabled: false,
          errors: [],
          attrs: { class: "form-control", rows: "12", spellcheck: "true" },
        },
        {
          name: "text_content",
          id: "id_text_content",
          widget: "textarea",
          value: "Hello {{ username }}",
          required: false,
          disabled: false,
          errors: [],
          attrs: { class: "form-control", rows: "12", spellcheck: "true" },
        },
      ],
    },
    compose: {
      selectedTemplateId: 7,
      templateOptions: [{ id: 7, name: "membership-request-rfi" }],
      availableVariables: [{ name: "username", example: "-username-" }],
      preview: {
        subject: "Hello -username-",
        html: "<p>Hello -username-</p>",
        text: "Hello -username-",
      },
    },
  },
};

describe("EmailTemplateEditorPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    delete (window as Window & { TemplatedEmailComposePreview?: unknown }).TemplatedEmailComposePreview;
    delete (window as Window & { TemplatedEmailComposeRegistry?: unknown }).TemplatedEmailComposeRegistry;
  });

  function loadTemplatedEmailComposeScript(): void {
    const scriptPath = resolve(process.cwd(), "../astra_app/core/static/core/js/templated_email.js");
    const script = readFileSync(scriptPath, "utf8");
    window.eval(script);
  }

  it("renders the editor shell from the data-only payload and reuses the compose DOM contract", async () => {
    const initAll = vi.fn();
    (window as Window & { TemplatedEmailComposeRegistry?: { initAll: typeof initAll } }).TemplatedEmailComposeRegistry = { initAll };

    const wrapper = mount(EmailTemplateEditorPage, {
      props: { bootstrap },
    });

    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(wrapper.find('form[action="/email-tools/templates/7/"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("Template details");
    expect(wrapper.text()).toContain("This template is referenced by the app configuration and cannot be renamed.");
    expect(wrapper.get("#id_name").attributes("disabled")).toBeDefined();
    expect(wrapper.find("[data-templated-email-compose]").exists()).toBe(true);
    expect(wrapper.get("[data-templated-email-compose]").attributes("data-compose-skip-initial-preview-refresh")).toBe("1");
    expect(wrapper.find('iframe[data-compose-preview-iframe="1"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("Cannot save right now.");
    expect(initAll).toHaveBeenCalled();
  });

  it("does not refresh preview on mount when bootstrap payload already provides the initial preview", async () => {
    vi.useFakeTimers();

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        html: "<p>Refreshed</p>",
        text: "Refreshed",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    window.fetch = fetchMock;

    loadTemplatedEmailComposeScript();

    mount(EmailTemplateEditorPage, {
      props: { bootstrap },
      attachTo: document.body,
    });

    await vi.runAllTimersAsync();

    expect(fetchMock).not.toHaveBeenCalled();

    const registry = window as Window & {
      TemplatedEmailComposeRegistry?: { getDefault?: () => unknown };
      TemplatedEmailComposePreview?: { refreshPreview?: (compose: unknown) => Promise<void> };
    };
    const compose = registry.TemplatedEmailComposeRegistry?.getDefault?.();

    expect(compose).not.toBeNull();

    await registry.TemplatedEmailComposePreview?.refreshPreview?.(compose);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});