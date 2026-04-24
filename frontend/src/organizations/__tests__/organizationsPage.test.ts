import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import OrganizationsPage from "../OrganizationsPage.vue";
import type { OrganizationsBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const bootstrap: OrganizationsBootstrap = {
  apiUrl: "/api/v1/organizations",
};

describe("OrganizationsPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads and renders top card plus sponsor and mirror cards", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          my_organization: null,
          my_organization_create_url: "/organizations/create/",
          sponsor_card: {
            title: "AlmaLinux Sponsor Members",
            q: "",
            empty_label: "No AlmaLinux sponsor members found.",
            items: [
              {
                id: 10,
                name: "Sponsor Org",
                status: "active",
                detail_url: "/organization/10/",
                logo_url: "",
                link_to_detail: false,
                memberships: [{ label: "Sponsor", class_name: "membership-standard", request_url: null }],
              },
            ],
            pagination: { count: 1, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 1 },
          },
          mirror_card: {
            title: "Mirror Sponsor Members",
            q: "",
            empty_label: "No mirror sponsor members found.",
            items: [
              {
                id: 20,
                name: "Mirror Org",
                status: "active",
                detail_url: "/organization/20/",
                logo_url: "",
                link_to_detail: false,
                memberships: [{ label: "Mirror", class_name: "membership-standard", request_url: null }],
              },
            ],
            pagination: { count: 1, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 1 },
          },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(OrganizationsPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalled();
    expect(wrapper.text()).toContain("My Organization");
    expect(wrapper.text()).toContain("Create organization");
    expect(wrapper.text()).toContain("AlmaLinux Sponsor Members");
    expect(wrapper.text()).toContain("Mirror Sponsor Members");
    expect(wrapper.text()).toContain("Sponsor Org");
    expect(wrapper.text()).toContain("Mirror Org");
    expect(wrapper.find('a[href="/organization/10/"]').exists()).toBe(false);
  });

  it("renders representative detail links and keeps sponsor badges before mirror badges", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          my_organization: {
            id: 1,
            name: "Alice Org",
            status: "active",
            detail_url: "/organization/1/",
            logo_url: "",
            link_to_detail: true,
            memberships: [
              { label: "Sponsor", class_name: "membership-standard", request_url: null },
              { label: "Mirror", class_name: "membership-standard", request_url: null },
            ],
          },
          my_organization_create_url: null,
          sponsor_card: {
            title: "AlmaLinux Sponsor Members",
            q: "",
            empty_label: "No AlmaLinux sponsor members found.",
            items: [
              {
                id: 10,
                name: "Sponsor Org",
                status: "active",
                detail_url: "/organization/10/",
                logo_url: "",
                link_to_detail: true,
                memberships: [
                  { label: "Sponsor", class_name: "membership-standard", request_url: null },
                  { label: "Mirror", class_name: "membership-standard", request_url: null },
                ],
              },
            ],
            pagination: { count: 1, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 1 },
          },
          mirror_card: {
            title: "Mirror Sponsor Members",
            q: "",
            empty_label: "No mirror sponsor members found.",
            items: [],
            pagination: { count: 0, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 0, end_index: 0 },
          },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(OrganizationsPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.find('a[href="/organization/1/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/organization/10/"]').exists()).toBe(true);

    const renderedText = wrapper.text();
    expect(renderedText.indexOf("Sponsor")).toBeLessThan(renderedText.indexOf("Mirror"));
  });

  it("keeps mirror card visible while sponsor search is loading", async () => {
    let fetchCall = 0;
    const fetchMock = vi.fn(async (_url: string) => {
      fetchCall += 1;
      if (fetchCall === 1) {
        return new Response(
          JSON.stringify({
            my_organization: null,
            my_organization_create_url: "/organizations/create/",
            sponsor_card: {
              title: "AlmaLinux Sponsor Members",
              q: "",
              empty_label: "No AlmaLinux sponsor members found.",
              items: [
                {
                  id: 10,
                  name: "Sponsor Org",
                  status: "active",
                  detail_url: "/organization/10/",
                  logo_url: "",
                  link_to_detail: false,
                  memberships: [{ label: "Sponsor", class_name: "membership-standard", request_url: null }],
                },
              ],
              pagination: { count: 1, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 1 },
            },
            mirror_card: {
              title: "Mirror Sponsor Members",
              q: "",
              empty_label: "No mirror sponsor members found.",
              items: [
                {
                  id: 20,
                  name: "Mirror Org",
                  status: "active",
                  detail_url: "/organization/20/",
                  logo_url: "",
                  link_to_detail: false,
                  memberships: [{ label: "Mirror", class_name: "membership-standard", request_url: null }],
                },
              ],
              pagination: { count: 1, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 1 },
            },
          }),
        );
      }

      return new Promise<Response>((resolve) => {
        setTimeout(() => {
          resolve(
            new Response(
              JSON.stringify({
                my_organization: null,
                my_organization_create_url: "/organizations/create/",
                sponsor_card: {
                  title: "AlmaLinux Sponsor Members",
                  q: "acme",
                  empty_label: "No AlmaLinux sponsor members found.",
                  items: [],
                  pagination: { count: 0, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 0, end_index: 0 },
                },
                mirror_card: {
                  title: "Mirror Sponsor Members",
                  q: "",
                  empty_label: "No mirror sponsor members found.",
                  items: [
                    {
                      id: 20,
                      name: "Mirror Org",
                      status: "active",
                      detail_url: "/organization/20/",
                      logo_url: "",
                      link_to_detail: false,
                      memberships: [{ label: "Mirror", class_name: "membership-standard", request_url: null }],
                    },
                  ],
                  pagination: { count: 1, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 1 },
                },
              }),
            ),
          );
        }, 50);
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(OrganizationsPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    const sponsorSearchInput = wrapper.find('input[name="q_sponsor"]');
    await sponsorSearchInput.setValue("acme");
    const sponsorForm = sponsorSearchInput.element.closest("form");
    sponsorForm?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await wrapper.vm.$nextTick();

    expect(wrapper.text()).toContain("Mirror Org");
  });
});
