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
  membershipRequestDetailTemplate: "/membership/request/__request_id__/",
  userProfileUrlTemplate: "/user/__username__/",
  sendMailUrlTemplate: "/email-tools/send-mail/?type=manual&to=__email__",
  membershipRequestUrl: "/organization/1/membership/request/",
  sponsorshipSetExpiryUrlTemplate: "/organization/1/sponsorship/__membership_type_code__/expiry/",
  sponsorshipTerminateUrlTemplate: "/organization/1/sponsorship/__membership_type_code__/terminate/",
  csrfToken: "csrf-token",
  nextUrl: "/organization/1/",
  expiryMinDate: "2026-04-01",
  membershipNotes: {
    summaryUrl: "/summary",
    detailUrl: "/detail",
    addUrl: "/add",
    csrfToken: "csrf",
    nextUrl: "/organization/1/",
    canView: true,
    canWrite: false,
  },
};

describe("OrganizationDetailPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders active and pending membership metadata with the shared card shell", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          organization: {
            id: 1,
            name: "Acme Org",
            status: "active",
            website: "https://example.com",
            logo_url: "",
            memberships: [
              {
                label: "Gold Sponsor Member",
                class_name: "membership-standard",
                request_url: null,
                description: "Annual sponsorship tier",
                member_since_label: "January 2024",
                expires_label: "Apr 30, 2026",
                expires_tone: "danger",
              },
            ],
            pending_memberships: [
              {
                request_id: 17,
                status: "pending",
                badge_label: "Under review",
                badge_class_name: "badge membership-under-review alx-status-badge alx-status-badge--review",
                membership_type: {
                  name: "Silver Sponsor Member",
                  code: "silver",
                  description: "Pending sponsor tier",
                  className: "membership-standard",
                },
              },
            ],
            representative: { username: "alice", full_name: "Alice Example" },
            contact_groups: [{ key: "business", label: "Business", name: "Business Person", email: "biz@example.com", phone: "" }],
            address: { street: "", city: "Durham", state: "", postal_code: "", country_code: "US" },
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
            props: ["targetType", "target", "requestDetailTemplate"],
            template: '<div data-test="notes-card">notes</div>',
          },
        },
      },
    });

    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalled();
    expect(wrapper.find("[data-membership-card-root]").exists()).toBe(true);
    expect(wrapper.text()).toContain("Member since January 2024");
    expect(wrapper.text()).toContain("Expires Apr 30, 2026");
    expect(wrapper.text()).toContain("Request #17");
    expect(wrapper.text()).toContain("Pending sponsor tier");
    expect(wrapper.text()).toContain("Under review");
    expect(wrapper.find('a[href="/membership/request/17/"]').exists()).toBe(true);
  });

  it("renders tier-change and expiration-management controls for active sponsorships", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          organization: {
            id: 1,
            name: "Acme Org",
            status: "active",
            website: "https://example.com",
            logo_url: "",
            memberships: [
              {
                label: "Gold Sponsor Member",
                class_name: "membership-standard",
                request_url: null,
                description: "Annual sponsorship tier",
                member_since_label: "January 2024",
                expires_label: "Apr 30, 2026",
                expires_tone: "muted",
                request_id: null,
                expires_on: "2026-04-30",
                can_request_tier_change: true,
                tier_change_membership_type_code: "ruby",
                can_manage_expiration: true,
                membership_type: {
                  name: "Gold Sponsor Member",
                  code: "gold",
                  description: "Annual sponsorship tier",
                  className: "membership-standard",
                },
              },
            ],
            pending_memberships: [],
            representative: { username: "alice", full_name: "Alice Example" },
            contact_groups: [{ key: "business", label: "Business", name: "Business Person", email: "biz@example.com", phone: "" }],
            address: { street: "", city: "Durham", state: "", postal_code: "", country_code: "US" },
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
            props: ["targetType", "target", "requestDetailTemplate"],
            template: '<div data-test="notes-card">notes</div>',
          },
        },
      },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.find('a[href="/organization/1/membership/request/?membership_type=ruby"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("Change tier");
    expect(wrapper.text()).toContain("Edit expiration");
    expect(wrapper.find('form[action="/organization/1/sponsorship/gold/expiry/"]').exists()).toBe(true);
    expect(wrapper.find('form[action="/organization/1/sponsorship/gold/terminate/"]').exists()).toBe(true);
    expect(wrapper.find('#sponsorship-expires-on-gold').attributes("value")).toBe("2026-04-30");
    expect(wrapper.find('input[name="csrfmiddlewaretoken"]').attributes("value")).toBe("csrf-token");
    expect(wrapper.text()).toContain("Manage membership: Gold Sponsor Member for Acme Org");
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
            logo_url: "",
            memberships: [{ label: "Gold Sponsor Member", class_name: "membership-standard", request_url: null, description: "", member_since_label: "", expires_label: "", expires_tone: "muted" }],
            pending_memberships: [],
            representative: { username: "alice", full_name: "Alice Example" },
            contact_groups: [{ key: "business", label: "Business", name: "Business Person", email: "biz@example.com", phone: "" }],
            address: { street: "", city: "Durham", state: "", postal_code: "", country_code: "US" },
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
            props: ["targetType", "target", "requestDetailTemplate"],
            template: '<div data-test="notes-card">{{ targetType }}:{{ target }}:{{ requestDetailTemplate }}</div>',
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
    expect(wrapper.find('a[href="/user/alice/"]').exists()).toBe(true);
    const contactTabs = wrapper.findAll("button.nav-link");
    await contactTabs[1]!.trigger("click");
    expect(wrapper.text()).toContain("biz@example.com");
    expect(wrapper.text()).toContain("Durham");
    expect(wrapper.find('[data-test="notes-card"]').text()).toBe("org:1:/membership/request/__request_id__/");
    const emailLink = wrapper.findAll("a").find((link) => link.text() === "biz@example.com");
    expect(emailLink?.attributes("href")).toBe("/email-tools/send-mail/?type=manual&to=biz%40example.com");
    expect(wrapper.find('a[href="/organization/1/edit/"]').exists()).toBe(false);
  });

  it("renders organization information full width before the membership row", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        return new Response(
          JSON.stringify({
            organization: {
              id: 1,
              name: "Acme Org",
              status: "active",
              website: "https://example.com",
              logo_url: "",
              memberships: [{ label: "Gold Sponsor Member", class_name: "membership-standard", request_url: null, description: "", member_since_label: "", expires_label: "", expires_tone: "muted" }],
              pending_memberships: [],
              representative: { username: "alice", full_name: "Alice Example" },
              contact_groups: [{ key: "business", label: "Business", name: "Business Person", email: "biz@example.com", phone: "" }],
              address: { street: "", city: "Durham", state: "", postal_code: "", country_code: "US" },
            },
          }),
        );
      }),
    );

    const wrapper = mount(OrganizationDetailPage, {
      props: { bootstrap },
      global: {
        stubs: {
          MembershipNotesCard: {
            props: ["targetType", "target", "requestDetailTemplate"],
            template: '<div data-test="notes-card">notes</div>',
          },
        },
      },
    });

    await flushPromises();
    await flushPromises();

    const html = wrapper.html();
    expect(wrapper.find("[data-membership-card-root]").exists()).toBe(true);
    expect(html.indexOf("Organization information")).toBeGreaterThan(html.indexOf("Contacts"));
    expect(html.indexOf("Membership")).toBeGreaterThan(html.indexOf("Organization information"));
    expect(html.indexOf("notes")).toBeGreaterThan(html.indexOf("Organization information"));
    expect(wrapper.find(".organization-info-card").classes()).not.toContain("col-md-5");
    expect(wrapper.find(".organization-info-card").element.closest(".col-12")?.className).toContain("col-12");
  });
});