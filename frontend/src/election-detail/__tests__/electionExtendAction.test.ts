import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import ElectionExtendAction from "../ElectionExtendAction.vue";
import type { ElectionExtendActionBootstrap } from "../types";

const bootstrap: ElectionExtendActionBootstrap = {
  extendApiUrl: "/api/v1/elections/1/extend-end",
  electionName: "Board election",
  currentEndDateTimeValue: "2026-04-10T10:00",
  currentEndDateTimeDisplay: "2026-04-10 10:00 UTC",
};

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

describe("ElectionExtendAction", () => {
  afterEach(() => {
    document.cookie = "";
    vi.restoreAllMocks();
  });

  it("opens the modal and requires a matching election name before submit", async () => {
    const wrapper = mount(ElectionExtendAction, {
      props: { bootstrap },
    });

    await wrapper.get("button.btn-warning").trigger("click");

    expect(wrapper.text()).toContain("Type the election name to confirm");
    const submit = wrapper.get("#extend-submit");
    expect(submit.attributes("disabled")).toBeDefined();

    await wrapper.get("#extend-confirm").setValue("wrong name");
    expect(wrapper.get("#extend-submit").attributes("disabled")).toBeDefined();

    await wrapper.get("#extend-confirm").setValue("Board election");
    expect(wrapper.get("#extend-submit").attributes("disabled")).toBeUndefined();
  });

  it("matches the legacy extend modal body spacing", async () => {
    const wrapper = mount(ElectionExtendAction, {
      props: { bootstrap },
    });

    await wrapper.get("button.btn-warning").trigger("click");

    expect(wrapper.get("#extend-end-datetime").element.closest(".form-group")?.className).toBe("form-group mb-0");
  });

  it("renders API errors when the extend request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ ok: false, errors: ["Invalid end datetime."] }), { status: 400 })),
    );

    const wrapper = mount(ElectionExtendAction, {
      props: { bootstrap },
    });

    await wrapper.get("button.btn-warning").trigger("click");
    await wrapper.get("#extend-confirm").setValue("Board election");
    await wrapper.get("#extend-end-datetime").setValue("2026-04-09T10:00");
    await wrapper.get("form").trigger("submit.prevent");
    await flushPromises();

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("Invalid end datetime.");
  });
});