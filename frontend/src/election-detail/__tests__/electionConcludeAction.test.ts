import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import ElectionConcludeAction from "../ElectionConcludeAction.vue";
import type { ElectionConcludeActionBootstrap } from "../types";

const bootstrap: ElectionConcludeActionBootstrap = {
  concludeApiUrl: "/api/v1/elections/1/conclude",
  electionName: "Board election",
  quorumWarning: "Quorum not met: 1 of 3 required voters have participated. Concluding now will mark this election as concluded without meeting quorum.",
};

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

describe("ElectionConcludeAction", () => {
  afterEach(() => {
    document.cookie = "";
    vi.restoreAllMocks();
  });

  it("opens the modal, shows quorum warning, and requires matching confirmation", async () => {
    const wrapper = mount(ElectionConcludeAction, {
      props: { bootstrap },
    });

    await wrapper.get("button.btn-danger").trigger("click");

    expect(wrapper.text()).toContain("Quorum not met:");
    expect(wrapper.get("#conclude-submit").attributes("disabled")).toBeDefined();

    await wrapper.get("#conclude-confirm").setValue("Board election");
    expect(wrapper.get("#conclude-submit").attributes("disabled")).toBeUndefined();
  });

  it("matches the legacy conclude modal body spacing", async () => {
    const wrapper = mount(ElectionConcludeAction, {
      props: { bootstrap },
    });

    await wrapper.get("button.btn-danger").trigger("click");

    expect(wrapper.get("#conclude-skip-tally").element.closest(".custom-control")?.className).toBe("custom-control custom-checkbox");
  });

  it("renders API errors when the conclude request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ ok: false, errors: ["Only open elections can be concluded."] }), { status: 400 })),
    );

    const wrapper = mount(ElectionConcludeAction, {
      props: { bootstrap },
    });

    await wrapper.get("button.btn-danger").trigger("click");
    await wrapper.get("#conclude-confirm").setValue("Board election");
    await wrapper.get("form").trigger("submit.prevent");
    await flushPromises();

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("Only open elections can be concluded.");
  });
});