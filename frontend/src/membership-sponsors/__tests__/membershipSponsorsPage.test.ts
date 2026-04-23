import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import MembershipSponsorsPage from "../MembershipSponsorsPage.vue";
import type { MembershipSponsorsBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const bootstrap: MembershipSponsorsBootstrap = {
  apiUrl: "/api/v1/membership/sponsors",
  pageSize: 25,
  initialQ: "",
};

describe("MembershipSponsorsPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads and renders sponsor rows", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          draw: 1,
          recordsTotal: 1,
          recordsFiltered: 1,
          data: [
            {
              membership_id: 17,
              organization: {
                id: 42,
                name: "Sponsor Org",
                url: "/organization/42/",
              },
              representative: {
                username: "repuser",
                full_name: "Representative User",
                display_label: "Representative User (repuser)",
                url: "/user/repuser/",
              },
              sponsorship_level: "Gold Sponsor",
              days_left: 5,
              is_expiring_soon: true,
              expires_display: "2026-04-28 (5 days left)",
              expires_at_order: "2026-04-28 23:59:59",
            },
          ],
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipSponsorsPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalled();
    expect(wrapper.text()).toContain("Sponsor Org");
    expect(wrapper.text()).toContain("Representative User (repuser)");
    expect(wrapper.text()).toContain("Gold Sponsor");
    expect(wrapper.text()).toContain("2026-04-28 (5 days left)");
    expect(wrapper.find('a[href="/organization/42/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/user/repuser/"]').exists()).toBe(true);
  });

  it("sends q to API, keeps URL state, and renders controls parity affordances", async () => {
    const historySpy = vi.spyOn(window.history, "replaceState");
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          draw: 1,
          recordsTotal: 0,
          recordsFiltered: 0,
          data: [],
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipSponsorsPage, {
      props: {
        bootstrap: {
          ...bootstrap,
          initialQ: "old",
        },
      },
    });

    await flushPromises();
    await flushPromises();

    await wrapper.get('input[name="q"]').setValue("new");
    await wrapper.get('form[data-sponsors-search-form]').trigger("submit");
    await flushPromises();
    await flushPromises();

    const fetchCalls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(fetchCalls.some((url) => url.includes("q=new"))).toBe(true);

    const lastCall = historySpy.mock.calls.at(-1);
    expect(String(lastCall?.[2])).toContain("q=new");

    expect(wrapper.find('[data-export-copy]').exists()).toBe(true);
    expect(wrapper.find('[data-export-csv]').exists()).toBe(true);
    expect(wrapper.find('[data-export-excel]').exists()).toBe(true);
    expect(wrapper.find('[data-export-pdf]').exists()).toBe(true);
    expect(wrapper.find('[data-export-print]').exists()).toBe(true);
    expect(wrapper.find('[data-colvis-toggle]').exists()).toBe(true);
  });
});
