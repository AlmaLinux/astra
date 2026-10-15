import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import SendMailPage from "../SendMailPage.vue";
import type { SendMailBootstrap } from "../types";

const initialPayload: NonNullable<SendMailBootstrap["initialPayload"]> = {
  selectedRecipientMode: "manual",
  actionStatus: "approved",
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
  attachments: [],
};

const bootstrap: SendMailBootstrap = {
  apiUrl: "/api/v1/email-tools/send-mail/detail",
  submitUrl: "/email-tools/send-mail/",
  previewUrl: "/email-tools/send-mail/render-preview/",
  attachmentsUploadUrl: "/email-tools/send-mail/attachments/",
  mailProgressApiUrl: "/api/v1/mail-progress?kind=send_mail",
  mailProgressAckApiUrl: "/api/v1/mail-progress/ack",
  csrfToken: "csrf-token",
  initialPayload,
};

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 20);
  });
}

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

  it("renders the compose controls and selects a newly created template from the payload", async () => {
    const wrapper = mount(SendMailPage, {
      props: {
        bootstrap: {
          ...bootstrap,
          initialPayload: {
            ...initialPayload,
            createdTemplateId: 32,
            templates: [
              { id: 31, name: "Original Send Mail Template" },
              { id: 32, name: "New Send Mail Template" },
            ],
            recipientPreview: {
              variables: [{ name: "full_name", example: "Alice User" }],
              recipientCount: 1,
              firstContext: { full_name: "Alice User" },
              skippedCount: 0,
            },
            compose: {
              selectedTemplateId: 32,
              preview: {
                subject: "Hello Alice User",
                html: "<p>Hello Alice User</p>",
                text: "Hello Alice User",
              },
            },
          },
        },
      },
      attachTo: document.body,
    });

    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(wrapper.text()).toContain("Available variables");
    expect(wrapper.find('[data-compose-action="copy-html-to-text"]').exists()).toBe(true);
    expect(wrapper.get("#send-mail-autoload-template-id").element).toHaveProperty("value", "32");
    const selectedTemplate = wrapper.get('select[name="email_template_id"] option:checked');
    expect(selectedTemplate.element).toHaveProperty("value", "32");
    expect(selectedTemplate.text()).toBe("New Send Mail Template");
  });

  it("adopts a delivery already running for this operator and reports its progress", async () => {
    const running = {
      state: "running" as const,
      total: 1200, processed: 300, emailed: 298, skipped: 2, failures: 0, message: "", updated_at: 0,
    };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ok: true, mail_progress: running }), { status: 200 })));

    const wrapper = mount(SendMailPage, { props: { bootstrap } });
    await flushPromises();

    expect(wrapper.get(".modal-title").text()).toBe("Sending emails...");
    expect(wrapper.get("[data-mail-progress-counts]").text()).toContain("300 of 1200 emails sent");
    expect(wrapper.get("[data-mail-progress-counts]").text()).toContain("2 skipped");
  });

  it("shows no progress dialog when the operator has no run in flight", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ok: true, mail_progress: null }), { status: 200 })));

    const wrapper = mount(SendMailPage, { props: { bootstrap } });
    await flushPromises();

    expect(wrapper.find("[data-mail-progress-counts]").exists()).toBe(false);
  });

  it("acknowledges a finished run so it stops being reported", async () => {
    const done = {
      state: "done" as const,
      total: 1200, processed: 1200, emailed: 1200, skipped: 0, failures: 0, message: "", updated_at: 0,
    };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, mail_progress: done }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(SendMailPage, { props: { bootstrap } });
    await flushPromises();

    expect(wrapper.get(".modal-title").text()).toBe("Emails sent");
    await wrapper.get("[data-mail-progress-continue]").trigger("click");
    await flushPromises();

    expect(wrapper.find("[data-mail-progress-counts]").exists()).toBe(false);
    expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("mail-progress/ack"))).toBe(true);
  });

  it("keeps attached files as references and lets the operator remove them", async () => {
    const wrapper = mount(SendMailPage, {
      props: {
        bootstrap: {
          ...bootstrap,
          initialPayload: {
            ...initialPayload,
            attachments: [
              { token: "tok-invoice", name: "AlmaLinux_OS_Foundation_Invoice_0042-2026-482915.pdf", size: 45122 },
              { token: "tok-agenda", name: "agenda.pdf", size: 2048 },
            ],
          },
        },
      },
    });
    await flushPromises();

    const options = wrapper.get("#send-mail-extra-options");
    // Attachments must be visible without the operator having to open the section.
    expect(options.classes()).toContain("show");
    const references = () => options.findAll('input[type="hidden"][name="attachment"]').map((input) => (input.element as HTMLInputElement).value);
    expect(references()).toEqual(["tok-invoice", "tok-agenda"]);
    expect(options.text()).toContain("AlmaLinux_OS_Foundation_Invoice_0042-2026-482915.pdf");
    expect(options.text()).toContain("44.1 KB");

    await options.get('button[aria-label="Remove agenda.pdf"]').trigger("click");

    expect(references()).toEqual(["tok-invoice"]);
    expect(options.text()).not.toContain("agenda.pdf");
  });

  it("offers a multi-file attachment input beside the address options", async () => {
    const wrapper = mount(SendMailPage, { props: { bootstrap } });
    await flushPromises();

    const columns = wrapper.get("#send-mail-extra-options").findAll(".row > .col-md-6");
    expect(columns).toHaveLength(2);
    expect(columns[0]!.find('input[name="cc"]').exists()).toBe(true);
    const fileInput = columns[1]!.get('input[type="file"]#send-mail-attachments');
    expect(fileInput.attributes("multiple")).toBeDefined();
    // Files are uploaded on selection, never again with the form.
    expect(fileInput.attributes("name")).toBeUndefined();
    expect(wrapper.get("#send-mail-extra-options").classes()).not.toContain("show");
  });

  it("places additional options below the recipient columns at full width", async () => {
    const wrapper = mount(SendMailPage, { props: { bootstrap } });
    await flushPromises();

    const options = wrapper.get("#send-mail-extra-options").element;
    const recipientColumns = wrapper.get("#send-mail-recipient-count").element.closest(".row") as HTMLElement;
    expect(recipientColumns.contains(options)).toBe(false);
    expect(recipientColumns.parentElement!.contains(options)).toBe(true);
    // Rendered after the recipient columns, not beside them.
    expect(recipientColumns.compareDocumentPosition(options) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  function pickFiles(input: HTMLInputElement, files: File[]): void {
    Object.defineProperty(input, "files", { value: files, configurable: true });
    input.dispatchEvent(new Event("change"));
  }

  it("uploads picked files right away and keeps the picker free for more", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      attachments: [{ token: "tok-notes", name: "notes.txt", size: 5 }],
    })));
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(SendMailPage, {
      props: {
        bootstrap: {
          ...bootstrap,
          initialPayload: { ...initialPayload, attachments: [{ token: "tok-agenda", name: "agenda.pdf", size: 2048 }] },
        },
      },
    });
    await flushPromises();

    const input = wrapper.get("#send-mail-attachments").element as HTMLInputElement;
    pickFiles(input, [new File(["notes"], "notes.txt", { type: "text/plain" })]);
    await flushPromises();

    // The page also polls delivery progress on load; only the upload matters here.
    const uploadCalls = (fetchMock.mock.calls as unknown as Array<[string, RequestInit]>).filter(
      ([url]) => url === "/email-tools/send-mail/attachments/",
    );
    expect(uploadCalls).toHaveLength(1);
    const [, init] = uploadCalls[0]!;
    expect((init.headers as Record<string, string>)["X-CSRFToken"]).toBe("csrf-token");
    const body = init.body as FormData;
    expect((body.getAll("files") as File[]).map((file) => file.name)).toEqual(["notes.txt"]);
    // The server checks size and name clashes against what is already attached.
    expect(body.getAll("attachment")).toEqual(["tok-agenda"]);

    const references = wrapper.findAll('input[type="hidden"][name="attachment"]').map((node) => (node.element as HTMLInputElement).value);
    expect(references).toEqual(["tok-agenda", "tok-notes"]);
    expect(input.value).toBe("");
    vi.unstubAllGlobals();
  });

  it("reports a rejected upload without losing the files already attached", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ error: "Attachments are too large: the total limit is 20 MB." }),
      { status: 400 },
    )));
    const wrapper = mount(SendMailPage, {
      props: {
        bootstrap: {
          ...bootstrap,
          initialPayload: { ...initialPayload, attachments: [{ token: "tok-agenda", name: "agenda.pdf", size: 2048 }] },
        },
      },
    });
    await flushPromises();

    pickFiles(wrapper.get("#send-mail-attachments").element as HTMLInputElement, [new File(["x"], "huge.bin")]);
    await flushPromises();

    expect(wrapper.get("#send-mail-extra-options").text()).toContain("Attachments are too large: the total limit is 20 MB.");
    expect(wrapper.findAll('input[type="hidden"][name="attachment"]')).toHaveLength(1);
    vi.unstubAllGlobals();
  });

  it("keeps what the operator typed when a file is attached", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      attachments: [{ token: "tok-notes", name: "notes.txt", size: 5 }],
    }))));
    const wrapper = mount(SendMailPage, { props: { bootstrap }, attachTo: document.body });
    await flushPromises();

    // The form's fields are plain inputs; a re-render of the page must not reset them.
    const cc = wrapper.get('input[name="cc"]').element as HTMLInputElement;
    cc.value = "typed@example.com";
    pickFiles(wrapper.get("#send-mail-attachments").element as HTMLInputElement, [new File(["notes"], "notes.txt")]);
    await flushPromises();

    expect(wrapper.findAll('input[type="hidden"][name="attachment"]')).toHaveLength(1);
    expect(cc.value).toBe("typed@example.com");
    wrapper.unmount();
    vi.unstubAllGlobals();
  });
});
