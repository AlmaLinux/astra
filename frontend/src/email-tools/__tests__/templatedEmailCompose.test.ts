import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

function loadTemplatedEmailComposeScript(): void {
  const scriptPath = resolve(process.cwd(), "../astra_app/core/static/core/js/templated_email.js");
  window.eval(readFileSync(scriptPath, "utf8"));
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
});