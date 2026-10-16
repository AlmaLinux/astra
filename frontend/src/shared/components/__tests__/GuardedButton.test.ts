import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import GuardedButton from "../GuardedButton.vue";

describe("GuardedButton", () => {
  it("stays disabled and explains why while its action is blocked", () => {
    const wrapper = mount(GuardedButton, {
      props: { blockedReason: "Enter a reason first.", title: "Save" },
      attrs: { type: "submit", class: "btn btn-primary" },
      slots: { default: "Save dues" },
    });

    const button = wrapper.get("button");
    expect(button.attributes("disabled")).toBeDefined();
    expect(button.attributes("type")).toBe("submit");
    expect(button.classes()).toContain("btn-primary");
    // Disabled buttons get no hover events, so the tooltip lives on the wrapper.
    expect(wrapper.attributes("title")).toBe("Enter a reason first.");
    expect(button.attributes("style")).toContain("pointer-events: none");
  });

  it("enables with its normal tooltip once the action is possible", async () => {
    const onClick = vi.fn();
    const wrapper = mount(GuardedButton, {
      props: { blockedReason: "", title: "Save" },
      attrs: { onClick },
      slots: { default: "Save dues" },
    });

    expect(wrapper.get("button").attributes("disabled")).toBeUndefined();
    expect(wrapper.attributes("title")).toBe("Save");

    await wrapper.get("button").trigger("click");
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("is disabled without a reason while busy", () => {
    const wrapper = mount(GuardedButton, { props: { blockedReason: "", title: "Save", busy: true } });

    expect(wrapper.get("button").attributes("disabled")).toBeDefined();
    expect(wrapper.attributes("title")).toBe("Save");
  });

  it("can use a layout element as its wrapper", () => {
    const wrapper = mount(GuardedButton, { props: { blockedReason: "", title: "Apply", wrapperClass: "input-group-append" } });

    expect(wrapper.classes()).toEqual(["input-group-append"]);
  });
});
