import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import ElectionTallyAction from "../ElectionTallyAction.vue";
import type { ElectionTallyActionBootstrap } from "../types";

const bootstrap: ElectionTallyActionBootstrap = {
  tallyApiUrl: "/api/v1/elections/1/tally",
  electionName: "Board election",
};

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

describe("ElectionTallyAction", () => {
  afterEach(() => {
    document.cookie = "";
    vi.restoreAllMocks();
  });

  it("opens the modal and requires matching confirmation", async () => {
    const wrapper = mount(ElectionTallyAction, {
      props: { bootstrap },
    });

    await wrapper.get("button.btn-primary").trigger("click");

    expect(wrapper.get("#tally-submit").attributes("disabled")).toBeDefined();

    await wrapper.get("#tally-confirm").setValue("Board election");
    expect(wrapper.get("#tally-submit").attributes("disabled")).toBeUndefined();
  });

  it("submits to the tally endpoint and reloads on success", async () => {
    const reload = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { reload },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ ok: true, message: "Election tallied." }), { status: 200 })),
    );

    const wrapper = mount(ElectionTallyAction, {
      props: { bootstrap },
    });

    await wrapper.get("button.btn-primary").trigger("click");
    await wrapper.get("#tally-confirm").setValue("Board election");
    await wrapper.get("form").trigger("submit.prevent");
    await flushPromises();

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/elections/1/tally",
      expect.objectContaining({ method: "POST" }),
    );
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("renders generic errors when the tally request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ ok: false, errors: ["Only closed elections can be tallied."] }), { status: 400 })),
    );

    const wrapper = mount(ElectionTallyAction, {
      props: { bootstrap },
    });

    await wrapper.get("button.btn-primary").trigger("click");
    await wrapper.get("#tally-confirm").setValue("Board election");
    await wrapper.get("form").trigger("submit.prevent");
    await flushPromises();

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("Only closed elections can be tallied.");
  });
});
