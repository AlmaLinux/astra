import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import GroupsPage from "../GroupsPage.vue";
import type { GroupsBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const bootstrap: GroupsBootstrap = {
  apiUrl: "/api/v1/groups",
  detailUrlTemplate: "/group/__group_name__/",
};

describe("GroupsPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads and renders groups table rows", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          q: "",
          items: [
            {
              cn: "infra",
              description: "Infrastructure",
              member_count: 12,
            },
          ],
          pagination: {
            count: 1,
            page: 1,
            num_pages: 1,
            page_numbers: [1],
            show_first: false,
            show_last: false,
            has_previous: false,
            has_next: false,
            previous_page_number: null,
            next_page_number: null,
            start_index: 1,
            end_index: 1,
          },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(GroupsPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalled();
    expect(wrapper.text()).toContain("infra");
    expect(wrapper.text()).toContain("Infrastructure");
    expect(wrapper.text()).toContain("12");
    expect(wrapper.find('a[href="/group/infra/"]').exists()).toBe(true);
  });

  it("sends q query when searching", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => {
      return new Response(
        JSON.stringify({
          q: "infra",
          items: [],
          pagination: {
            count: 0,
            page: 1,
            num_pages: 1,
            page_numbers: [1],
            show_first: false,
            show_last: false,
            has_previous: false,
            has_next: false,
            previous_page_number: null,
            next_page_number: null,
            start_index: 0,
            end_index: 0,
          },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(GroupsPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    await wrapper.get('input[name="q"]').setValue("infra");
    await wrapper.get('form').trigger("submit");
    await flushPromises();
    await flushPromises();

    const fetchCalls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(fetchCalls.some((url) => url.includes("q=infra"))).toBe(true);
  });
});
