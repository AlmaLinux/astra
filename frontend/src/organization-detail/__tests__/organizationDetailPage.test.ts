import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import OrganizationDetailPage from "../OrganizationDetailPage.vue";
import type { OrganizationDetailBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const bootstrap: OrganizationDetailBootstrap = {
  apiUrl: "/api/v1/organizations/1",
};

describe("OrganizationDetailPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads and renders organization summary details", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          organization: {
            id: 1,
            name: "Acme Org",
            status: "active",
            website: "https://example.com",
            detail_url: "/organizations/1/",
            logo_url: "",
            memberships: [{ label: "Gold Sponsor Member", class_name: "membership-standard", request_url: null }],
            representative: { username: "alice", full_name: "Alice Example" },
            contact_groups: [{ key: "business", label: "Business", name: "Business Person", email: "biz@example.com", phone: "" }],
            address: { street: "", city: "Durham", state: "", postal_code: "", country_code: "US" },
            notes: { summaryUrl: "/summary", detailUrl: "/detail", addUrl: "/add", csrfToken: "csrf", nextUrl: "/organization/1/", canView: true, canWrite: false, targetType: "org", target: "1" },
          },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(OrganizationDetailPage, {
      props: { bootstrap },
      global: {
        stubs: {
          MembershipNotesCard: {
            props: ["targetType", "target"],
            template: '<div data-test="notes-card">{{ targetType }}:{{ target }}</div>',
          },
        },
      },
    });

    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalled();
    expect(wrapper.text()).toContain("Acme Org");
    expect(wrapper.text()).toContain("Gold Sponsor Member");
    expect(wrapper.text()).toContain("Alice Example");
    const contactTabs = wrapper.findAll("button.nav-link");
    await contactTabs[1]!.trigger("click");
    expect(wrapper.text()).toContain("biz@example.com");
    expect(wrapper.text()).toContain("Durham");
    expect(wrapper.find('[data-test="notes-card"]').text()).toBe("org:1");
    expect(wrapper.find('a[href="/organization/1/edit/"]').exists()).toBe(false);
  });
});