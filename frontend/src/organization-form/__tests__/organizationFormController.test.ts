import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import OrganizationFormController from "../OrganizationFormController.vue";

describe("OrganizationFormController", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("initializes select2 on representative fields with ajax configuration", async () => {
    document.body.innerHTML = `
      <form>
        <select class="alx-select2" data-ajax-url="/organizations/representatives/search/"></select>
      </form>
    `;

    const select2Mock = vi.fn();
    const dataMock = vi.fn(() => undefined);
    const jqMock = vi.fn(() => ({
      data: dataMock,
      attr: vi.fn(() => "/organizations/representatives/search/"),
      select2: select2Mock,
    }));
    (jqMock as unknown as { fn?: object }).fn = { select2: select2Mock };
    vi.stubGlobal("jQuery", jqMock);
    vi.stubGlobal("$", jqMock);

    mount(OrganizationFormController);

    expect(jqMock).toHaveBeenCalled();
    expect(select2Mock).toHaveBeenCalled();
  });

  it("activates the tab containing the first invalid field on submit", async () => {
    document.body.innerHTML = `
      <form>
        <ul id="contacts-tabs">
          <li><a class="nav-link active" href="#contacts-business">Business</a></li>
          <li><a class="nav-link" href="#contacts-technical">Technical</a></li>
        </ul>
        <div id="contacts-tab-content">
          <div id="contacts-business" class="tab-pane fade active show">
            <input type="text" value="ok" />
          </div>
          <div id="contacts-technical" class="tab-pane fade">
            <input id="invalid-email" type="email" value="invalid-email" />
          </div>
        </div>
      </form>
    `;

    mount(OrganizationFormController);

    const form = document.querySelector("form")!;
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));

    expect(document.querySelector('[href="#contacts-technical"]')?.classList.contains("alx-tab-error")).toBe(true);
  });
});