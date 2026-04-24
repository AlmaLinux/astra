import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import WidgetUser from "../WidgetUser.vue";

describe("WidgetUser", () => {
  it("builds its own profile URL from username", () => {
    const wrapper = mount(WidgetUser, {
      props: {
        username: "alice",
        fullName: "Alice Example",
      },
    });

    expect(wrapper.findAll('a[href="/user/alice/"]').length).toBe(2);
    expect(wrapper.text()).toContain("alice");
  });

  it("renders shared display state and action buttons", async () => {
    const onClick = vi.fn();
    const wrapper = mount(WidgetUser, {
      props: {
        username: "bob",
        dimmed: true,
        secondaryText: "Unsigned",
        actions: [
          {
            key: "remove-bob",
            ariaLabel: "Remove member",
            title: "Remove this member from the group",
            buttonClass: "btn btn-outline-danger btn-sm",
            iconClass: "fas fa-user-minus",
            onClick,
          },
        ],
      },
    });

    expect(wrapper.find(".widget-user").exists()).toBe(true);
    expect(wrapper.text()).toContain("Unsigned");
    expect(wrapper.find('button[aria-label="Remove member"]').exists()).toBe(true);

    await wrapper.get('button[aria-label="Remove member"]').trigger("click");
    expect(onClick).toHaveBeenCalledOnce();
  });
});
