import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

function loadTemplatedEmailComposeScript(): void {
  const scriptPath = resolve(process.cwd(), "../astra_app/core/static/core/js/templated_email.js");
  window.eval(readFileSync(scriptPath, "utf8"));
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolvePromise!: (value: T) => void;
  const promise = new Promise<T>((resolve) => {
    resolvePromise = resolve;
  });
  return { promise, resolve: resolvePromise };
}

describe("templated email compose", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    delete (window as Window & { TemplatedEmailCompose?: unknown }).TemplatedEmailCompose;
    delete (window as Window & { TemplatedEmailComposePreview?: unknown }).TemplatedEmailComposePreview;
    delete (window as Window & { TemplatedEmailComposeRegistry?: unknown }).TemplatedEmailComposeRegistry;
  });

  it("closes the save confirmation modal when the save form is confirmed", () => {
    document.body.innerHTML = `
      <form id="election-edit-form">
        <div class="templated-email-compose" data-templated-email-compose>
          <select name="email_template_id">
            <option value="7" selected>Election template</option>
          </select>
          <input name="subject" value="Subject">
          <textarea name="html_content"><p>HTML</p></textarea>
          <textarea name="text_content">Text</textarea>
          <button type="button" data-compose-action="save">Save</button>
          <div data-compose-modal="save">
            <div class="modal">
              <form>
                <button type="submit">Save</button>
              </form>
            </div>
          </div>
          <div data-compose-preview="html"><iframe data-compose-preview-iframe="1"></iframe></div>
          <div data-compose-preview="text"><iframe data-compose-preview-iframe="1"></iframe></div>
        </div>
      </form>
    `;

    const modalActions: string[] = [];
    const jquery = Object.assign(
      (target: Element) => ({
        modal: (action: string) => {
          modalActions.push(action);
          target.classList.toggle("show", action === "show");
        },
      }),
      { fn: { modal: vi.fn() } },
    );
    vi.stubGlobal("jQuery", jquery);

    loadTemplatedEmailComposeScript();

    const modal = document.querySelector<HTMLElement>('[data-compose-modal="save"] .modal');
    const saveButton = document.querySelector<HTMLButtonElement>('[data-compose-action="save"]');
    const modalSaveButton = modal?.querySelector<HTMLButtonElement>('button[type="submit"]');

    saveButton?.click();
    modalSaveButton?.click();

    expect(modalActions).toEqual(["show", "hide"]);
    expect(modal?.classList.contains("show")).toBe(false);
  });

  it("keeps the preview for the newest text edit when render responses complete out of order", async () => {
    document.body.innerHTML = `
      <div class="templated-email-compose" data-templated-email-compose data-compose-preview-url="/elections/1/email/render-preview/">
        <input name="subject" value="Subject">
        <textarea name="html_content">First HTML</textarea>
        <textarea name="text_content">First text</textarea>
        <div data-compose-preview="html"><iframe data-compose-preview-iframe="1"></iframe></div>
        <div data-compose-preview="text"><iframe data-compose-preview-iframe="1"></iframe></div>
      </div>
    `;
    const firstResponse = deferred<Response>();
    const secondResponse = deferred<Response>();
    const fetchMock = vi.fn()
      .mockReturnValueOnce(firstResponse.promise)
      .mockReturnValueOnce(secondResponse.promise);
    vi.stubGlobal("fetch", fetchMock);

    loadTemplatedEmailComposeScript();

    const textArea = document.querySelector<HTMLTextAreaElement>('textarea[name="text_content"]');
    const registry = window as Window & {
      TemplatedEmailComposeRegistry?: { getDefault?: () => unknown };
      TemplatedEmailComposePreview?: { refreshPreview?: (compose: unknown) => Promise<void> };
    };
    const compose = registry.TemplatedEmailComposeRegistry?.getDefault?.();

    expect(compose).not.toBeNull();

    textArea!.value = "Older text";
    const olderPreview = registry.TemplatedEmailComposePreview?.refreshPreview?.(compose);

    textArea!.value = "Newest text";
    const newerPreview = registry.TemplatedEmailComposePreview?.refreshPreview?.(compose);

    secondResponse.resolve({
      ok: true,
      json: async () => ({ html: "<p>Newest HTML</p>", text: "Newest text" }),
    } as Response);
    await newerPreview;

    firstResponse.resolve({
      ok: true,
      json: async () => ({ html: "<p>Older HTML</p>", text: "Older text" }),
    } as Response);
    await olderPreview;

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(document.querySelector<HTMLIFrameElement>('[data-compose-preview="text"] iframe')?.srcdoc).toContain("Newest text");
  });

  it("refreshes the rendered text sample after typing without saving the draft", async () => {
    vi.useFakeTimers();
    document.body.innerHTML = `
      <div class="templated-email-compose" data-templated-email-compose data-compose-preview-url="/elections/1/email/render-preview/">
        <input name="subject" value="Subject">
        <textarea name="html_content">HTML</textarea>
        <textarea name="text_content">Original text</textarea>
        <div data-compose-preview="html"><iframe data-compose-preview-iframe="1"></iframe></div>
        <div data-compose-preview="text"><iframe data-compose-preview-iframe="1"></iframe></div>
      </div>
    `;
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ html: "<p>Rendered HTML</p>", text: "Rendered text" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    loadTemplatedEmailComposeScript();

    const textArea = document.querySelector<HTMLTextAreaElement>('textarea[name="text_content"]');
    textArea!.value = "Changed text";
    textArea?.dispatchEvent(new Event("input", { bubbles: true }));
    await vi.advanceTimersByTimeAsync(50);

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const body = request.body as FormData;

    expect(fetchMock).toHaveBeenCalledWith("/elections/1/email/render-preview/", expect.objectContaining({ method: "POST" }));
    expect(body.get("text_content")).toBe("Changed text");
    expect(document.querySelector<HTMLIFrameElement>('[data-compose-preview="text"] iframe')?.srcdoc).toContain("Rendered text");
  });

});
