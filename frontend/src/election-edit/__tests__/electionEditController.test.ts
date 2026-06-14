import { beforeEach, describe, expect, it, vi } from "vitest";

import { initElectionEditController } from "../controller";

describe("initElectionEditController", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("adds candidate rows from the empty-form template and marks removed rows as deleted", () => {
    document.body.innerHTML = `
      <input id="id_candidates-TOTAL_FORMS" value="0" />
      <table><tbody id="candidates-formset-body"></tbody></table>
      <button type="button" id="candidates-add-row">Add candidate</button>
      <template id="candidates-empty-form">
        <tr class="candidate-form-row">
          <td>
            <input name="candidates-__prefix__-freeipa_username" />
          </td>
          <td>
            <input type="checkbox" name="candidates-__prefix__-DELETE" />
            <button type="button" class="election-edit-remove-row">Remove</button>
          </td>
        </tr>
      </template>
      <input id="id_groups-TOTAL_FORMS" value="0" />
      <table><tbody id="groups-formset-body"></tbody></table>
      <template id="groups-empty-form"></template>
    `;

    initElectionEditController();

    const addButton = document.getElementById("candidates-add-row");
    addButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    const totalForms = document.getElementById("id_candidates-TOTAL_FORMS") as HTMLInputElement;
    const addedRow = document.querySelector("#candidates-formset-body tr") as HTMLTableRowElement;
    expect(totalForms.value).toBe("1");
    expect(addedRow).not.toBeNull();
    expect(addedRow.querySelector('input[name="candidates-0-freeipa_username"]')).not.toBeNull();

    const removeButton = addedRow.querySelector(".election-edit-remove-row");
    removeButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    const deleteField = addedRow.querySelector('input[name="candidates-0-DELETE"]') as HTMLInputElement;
    expect(deleteField.checked).toBe(true);
    expect(addedRow.style.display).toBe("none");
  });

  it("prompts before saving when the draft template selection changed", () => {
    document.body.innerHTML = `
      <form id="election-edit-form">
        <input type="hidden" id="election-edit-action" value="save_draft" />
        <input type="hidden" id="election-edit-email-save-mode" value="" />
        <input type="hidden" id="election-edit-has-election" value="1" />
        <input type="hidden" id="election-edit-election-status" value="draft" />
        <input type="hidden" id="election-edit-original-email-template-id" value="1" />
      </form>
      <button type="button" id="edit-keep-existing-email-btn">Keep</button>
      <button type="button" id="edit-save-email-btn">Save</button>
      <div id="edit-template-changed-modal"></div>
      <input id="id_candidates-TOTAL_FORMS" value="0" />
      <template id="candidates-empty-form"></template>
      <tbody id="candidates-formset-body"></tbody>
      <input id="id_groups-TOTAL_FORMS" value="0" />
      <template id="groups-empty-form"></template>
      <tbody id="groups-formset-body"></tbody>
    `;

    const modal = vi.fn();
    vi.stubGlobal("jQuery", Object.assign(
      (selector: string | Element) => ({
        modal,
        on: vi.fn(),
        fn: { modal },
        closest: () => [],
      }),
      { fn: { modal } },
    ));

    vi.stubGlobal("TemplatedEmailCompose", {
      getTemplateSelectEl: () => {
        const select = document.createElement("select");
        select.value = "2";
        return select;
      },
      getValues: () => ({ subject: "", html_content: "", text_content: "" }),
      setRestoreEnabled: vi.fn(),
      markBaseline: vi.fn(),
      getCsrfToken: () => "token",
      getField: () => "",
    });

    initElectionEditController();

    const form = document.getElementById("election-edit-form") as HTMLFormElement;
    const event = new Event("submit", { bubbles: true, cancelable: true });
    form.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(modal).toHaveBeenCalledWith("show");
  });

  it("does not prompt when compose registers after init and the template id still matches", () => {
    document.body.innerHTML = `
      <form id="election-edit-form">
        <input type="hidden" id="election-edit-action" value="save_draft" />
        <input type="hidden" id="election-edit-email-save-mode" value="" />
        <input type="hidden" id="election-edit-has-election" value="1" />
        <input type="hidden" id="election-edit-election-status" value="draft" />
        <input type="hidden" id="election-edit-original-email-template-id" value="17" />
      </form>
      <button type="button" id="edit-keep-existing-email-btn">Keep</button>
      <button type="button" id="edit-save-email-btn">Save</button>
      <div id="edit-template-changed-modal"></div>
      <input id="id_candidates-TOTAL_FORMS" value="0" />
      <template id="candidates-empty-form"></template>
      <tbody id="candidates-formset-body"></tbody>
      <input id="id_groups-TOTAL_FORMS" value="0" />
      <template id="groups-empty-form"></template>
      <tbody id="groups-formset-body"></tbody>
    `;

    const modal = vi.fn();
    vi.stubGlobal("jQuery", Object.assign(
      () => ({
        modal,
        on: vi.fn(),
        fn: { modal },
        closest: () => [],
      }),
      { fn: { modal } },
    ));

    delete (window as Window & { TemplatedEmailCompose?: unknown }).TemplatedEmailCompose;

    initElectionEditController();

    const select = document.createElement("select");
    select.name = "email_template_id";
    select.append(new Option("Existing", "17", true, true));
    select.value = "17";

    vi.stubGlobal("TemplatedEmailCompose", {
      getTemplateSelectEl: () => select,
      getValues: () => ({ subject: "", html_content: "", text_content: "" }),
      setRestoreEnabled: vi.fn(),
      markBaseline: vi.fn(),
      getCsrfToken: () => "token",
      getField: () => "",
    });

    const form = document.getElementById("election-edit-form") as HTMLFormElement;
    const event = new Event("submit", { bubbles: true, cancelable: true });
    form.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(false);
    expect(modal).not.toHaveBeenCalled();
  });

  it("triggers change after syncing group candidate options", () => {
    document.body.innerHTML = `
      <input id="id_candidates-TOTAL_FORMS" value="1" />
      <table>
        <tbody id="candidates-formset-body">
          <tr>
            <td>
              <select name="candidates-0-freeipa_username">
                <option value="alice" selected>Alice User</option>
                <option value="bob">Bob User</option>
              </select>
              <select name="candidates-0-nominated_by"></select>
              <input type="checkbox" name="candidates-0-DELETE" />
            </td>
          </tr>
        </tbody>
      </table>
      <template id="candidates-empty-form"></template>
      <input id="id_groups-TOTAL_FORMS" value="1" />
      <table>
        <tbody id="groups-formset-body">
          <tr>
            <td>
              <select name="groups-0-candidate_usernames" multiple>
                <option value="alice" selected>Alice User</option>
              </select>
            </td>
          </tr>
        </tbody>
      </table>
      <template id="groups-empty-form"></template>
    `;

    initElectionEditController();

    const groupSelect = document.querySelector<HTMLSelectElement>('select[name="groups-0-candidate_usernames"]');
    const candidateSelect = document.querySelector<HTMLSelectElement>('select[name="candidates-0-freeipa_username"]');
    const groupChange = vi.fn();
    groupSelect?.addEventListener("change", groupChange);

    if (candidateSelect) {
      candidateSelect.value = "bob";
      candidateSelect.dispatchEvent(new Event("change", { bubbles: true }));
    }

    expect(groupChange).toHaveBeenCalled();
    expect(Array.from(groupSelect?.options ?? []).map((option) => option.value)).toEqual(["bob"]);
  });

  it("updates the start datetime eligibility cutoff help text using the UTC cutoff day", () => {
    document.body.innerHTML = `
      <div data-election-edit-root data-election-edit-min-membership-age-days="1"></div>
      <input id="id_start_datetime" value="2026-06-04T15:42" />
      <small data-election-edit-start-cutoff-help></small>
      <input id="id_candidates-TOTAL_FORMS" value="0" />
      <template id="candidates-empty-form"></template>
      <tbody id="candidates-formset-body"></tbody>
      <input id="id_groups-TOTAL_FORMS" value="0" />
      <template id="groups-empty-form"></template>
      <tbody id="groups-formset-body"></tbody>
    `;

    initElectionEditController();

    const helpText = document.querySelector<HTMLElement>("[data-election-edit-start-cutoff-help]");
    expect(helpText?.textContent).toBe("Eligibility cutoff date: 2026-06-03 00:00 UTC.");

    const startInput = document.getElementById("id_start_datetime") as HTMLInputElement;
    startInput.value = "2026-06-10T00:15";
    startInput.dispatchEvent(new Event("change", { bubbles: true }));

    expect(helpText?.textContent).toBe("Eligibility cutoff date: 2026-06-09 00:00 UTC.");
  });
});
