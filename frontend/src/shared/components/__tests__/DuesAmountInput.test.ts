import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import DuesAmountInput from "../DuesAmountInput.vue";

describe("DuesAmountInput", () => {
  it("drops anything but digits as it is typed", async () => {
    const wrapper = mount(DuesAmountInput, { props: { id: "dues", modelValue: "", defaultUsd: 20000 } });
    const input = wrapper.get<HTMLInputElement>("input");

    await input.setValue("10aa");

    expect(input.element.value).toBe("10");
    expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual(["10"]);
  });

  it("flags amounts above the standard price", async () => {
    const wrapper = mount(DuesAmountInput, { props: { id: "dues", modelValue: "25000", defaultUsd: 20000 } });

    expect(wrapper.get("input").classes()).toContain("is-invalid");
    expect(wrapper.text()).toContain("The annual dues can't exceed the standard price of $20,000.");

    await wrapper.setProps({ modelValue: "20000" });
    expect(wrapper.get("input").classes()).not.toContain("is-invalid");
  });

  it("explains that an empty field charges the standard price", () => {
    const wrapper = mount(DuesAmountInput, { props: { id: "dues", modelValue: "", defaultUsd: 20000 } });

    expect(wrapper.text()).toContain("Leave empty to charge the standard price of $20,000");
    expect(wrapper.get("input").attributes("placeholder")).toBe("$20,000 (standard price)");
  });
});
