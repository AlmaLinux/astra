import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import ElectionsPage from "../ElectionsPage.vue";
import type { ElectionsBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const bootstrap: ElectionsBootstrap = {
  apiUrl: "/api/v1/elections",
};

describe("ElectionsPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads and renders open and past elections in separate sections", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          can_manage_elections: true,
          items: [
            {
              id: 1,
              name: "Board election",
              description: "Foundation board",
              status: "open",
              start_datetime: "2026-04-01T10:00:00+00:00",
              end_datetime: "2026-04-10T10:00:00+00:00",
              detail_url: "/elections/1/",
              edit_url: "/elections/1/edit/",
            },
            {
              id: 2,
              name: "Past election",
              description: "Already counted",
              status: "tallied",
              start_datetime: "2026-03-01T10:00:00+00:00",
              end_datetime: "2026-03-05T10:00:00+00:00",
              detail_url: "/elections/2/",
              edit_url: null,
            },
          ],
          pagination: {
            count: 2,
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
            end_index: 2,
          },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ElectionsPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalled();
    expect(wrapper.text()).toContain("Open elections");
    expect(wrapper.text()).toContain("Past elections");
    expect(wrapper.text()).toContain("Board election");
    expect(wrapper.text()).toContain("Past election");
    expect(wrapper.find('a[href="/elections/1/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/elections/2/"]').exists()).toBe(true);
    expect(wrapper.find(".list-group").exists()).toBe(true);
    expect(wrapper.findAll(".list-group-item")).toHaveLength(2);
    expect(wrapper.find("table").exists()).toBe(false);
    expect(wrapper.find('[title="Show or hide past elections"]').exists()).toBe(true);
  });

  it("links draft elections to edit for managers", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            can_manage_elections: true,
            items: [
              {
                id: 9,
                name: "Draft election",
                description: "",
                status: "draft",
                start_datetime: "2026-04-01T10:00:00+00:00",
                end_datetime: "2026-04-10T10:00:00+00:00",
                detail_url: "/elections/9/",
                edit_url: "/elections/9/edit/",
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
        ),
      ),
    );

    const wrapper = mount(ElectionsPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.find('a[href="/elections/9/edit/"]').exists()).toBe(true);
  });
});