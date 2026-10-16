import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { defineComponent, ref } from "vue";

import { useComposeSaveHandlers } from "../compose-actions";

describe("useComposeSaveHandlers", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("confirms a new template in the compose notice modal instead of a browser alert", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ok: true, id: 9, name: "Reminder" }))));
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    const host = mount(defineComponent({
      setup() {
        useComposeSaveHandlers({ templateOptions: ref([]), selectedTemplateId: ref(null) });
        return () => null;
      },
    }));
    const compose = { getValues: () => ({ subject: "S", html_content: "H", text_content: "T" }), getCsrfToken: () => "token", notify: vi.fn() };

    document.dispatchEvent(new CustomEvent("templated-email-compose:save-as-confirmed", { detail: { instance: compose, name: "Reminder" } }));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(compose.notify).toHaveBeenCalledWith("Template created", 'Created "Reminder" from the current subject and contents.');
    expect(alert).not.toHaveBeenCalled();
    host.unmount();
  });
});
