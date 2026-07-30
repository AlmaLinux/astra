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
  apiUrl: "/api/v1/organizations/1/detail",
  membershipRequestDetailTemplate: "/membership/request/__request_id__/",
  userProfileUrlTemplate: "/user/__username__/",
  sendMailUrlTemplate: "/email-tools/send-mail/?type=manual&to=__email__",
  membershipRequestUrl: "/organization/1/membership/request/",
  membershipHistoryUrl: "/membership/log/org/1/",
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
                requestId: null,
                kind: "membership" as const,
                key: "membership-gold",
                membershipType: {
                  name: "Gold Sponsor Member",
                  code: "gold",
                  description: "Annual sponsorship tier",
                },
                createdAt: "2024-01-15T12:00:00Z",
                expiresAt: "2026-04-30T00:00:00Z",
                isExpiringSoon: true,
                canRequestTierChange: false,
                tierChangeMembershipTypeCode: "",
                canManage: false,
              },
            ],
            pending_memberships: [
              {
                kind: "pending" as const,
                key: "pending-17",
                requestId: 17,
                organizationName: "",
                status: "pending",
                membershipType: {
                  name: "Silver Sponsor Member",
                  code: "silver",
                  description: "Pending sponsor tier",
                },
              },
            ],
            representative: { username: "alice", full_name: "Alice Example" },
            contact_groups: [{ key: "business", name: "Business Person", email: "biz@example.com", phone: "" }],
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
    expect(wrapper.find(".membership-under-review.alx-status-badge--review").exists()).toBe(true);
    expect(wrapper.find('a[href="/membership/request/17/"]').exists()).toBe(true);
  });

  it("preserves representative action-required copy for on-hold pending sponsorships", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          organization: {
            id: 1,
            name: "Acme Org",
            status: "active",
            is_representative: true,
            website: "https://example.com",
            logo_url: "",
            memberships: [],
            pending_memberships: [
              {
                kind: "pending" as const,
                key: "pending-17",
                requestId: 17,
                organizationName: "",
                status: "on_hold",
                membershipType: {
                  name: "Silver Sponsor Member",
                  code: "silver",
                  description: "Pending sponsor tier",
                },
              },
            ],
            representative: { username: "alice", full_name: "Alice Example" },
            contact_groups: [],
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
    expect(wrapper.text()).toContain("Action required");
    expect(wrapper.find(".membership-action-required.alx-status-badge--action").exists()).toBe(true);
  });

  it("renders on-hold copy for non-representative viewers", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          organization: {
            id: 1,
            name: "Acme Org",
            status: "active",
            is_representative: false,
            website: "https://example.com",
            logo_url: "",
            memberships: [],
            pending_memberships: [
              {
                kind: "pending" as const,
                key: "pending-17",
                requestId: 17,
                organizationName: "",
                status: "on_hold",
                membershipType: {
                  name: "Silver Sponsor Member",
                  code: "silver",
                  description: "Pending sponsor tier",
                },
              },
            ],
            representative: { username: "alice", full_name: "Alice Example" },
            contact_groups: [],
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
    expect(wrapper.text()).toContain("On hold");
    expect(wrapper.text()).not.toContain("Action required");
    expect(wrapper.find(".membership-action-required.alx-status-badge--action").exists()).toBe(true);
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
                requestId: null,
                kind: "membership" as const,
                key: "membership-gold",
                membershipType: {
                  name: "Gold Sponsor Member",
                  code: "gold",
                  description: "Annual sponsorship tier",
                },
                createdAt: "2024-01-15T12:00:00Z",
                expiresAt: "2026-04-30T00:00:00Z",
                isExpiringSoon: false,
                canRequestTierChange: true,
                tierChangeMembershipTypeCode: "ruby",
                canManage: true,
              },
            ],
            pending_memberships: [],
            representative: { username: "alice", full_name: "Alice Example" },
            contact_groups: [{ key: "business", name: "Business Person", email: "biz@example.com", phone: "" }],
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

  it("renders the renewal CTA for an expiring sponsorship the viewer may request", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          organization: {
            id: 1,
            name: "Acme Org",
            status: "active",
            is_representative: true,
            website: "https://example.com",
            logo_url: "",
            memberships: [
              {
                requestId: null,
                kind: "membership" as const,
                key: "membership-gold",
                membershipType: {
                  name: "Gold Sponsor Member",
                  code: "gold",
                  description: "Annual sponsorship tier",
                },
                createdAt: "2024-01-15T12:00:00Z",
                expiresAt: "2026-04-30T00:00:00Z",
                isExpiringSoon: true,
                canRenew: true,
                renewalMembershipTypeCode: "gold",
                canRequestTierChange: true,
                tierChangeMembershipTypeCode: "ruby",
                canManage: false,
              },
            ],
            pending_memberships: [],
            representative: { username: "alice", full_name: "Alice Example" },
            contact_groups: [],
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

    const renewalLink = wrapper.findAll("a").find((link) => link.text() === "Request renewal");
    expect(renewalLink?.attributes("href")).toBe("/organization/1/membership/request/?membership_type=gold");
    // Renewal targets the held tier; the tier-change CTA still points at a different tier.
    expect(wrapper.find('a[href="/organization/1/membership/request/?membership_type=ruby"]').exists()).toBe(true);
  });

  it("omits the renewal CTA when the backend withholds can_renew", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          organization: {
            id: 1,
            name: "Acme Org",
            status: "active",
            is_representative: false,
            website: "https://example.com",
            logo_url: "",
            memberships: [
              {
                requestId: null,
                kind: "membership" as const,
                key: "membership-gold",
                membershipType: {
                  name: "Gold Sponsor Member",
                  code: "gold",
                  description: "Annual sponsorship tier",
                },
                createdAt: "2024-01-15T12:00:00Z",
                expiresAt: "2026-04-30T00:00:00Z",
                isExpiringSoon: true,
                canRenew: false,
                renewalMembershipTypeCode: "",
                canRequestTierChange: false,
                tierChangeMembershipTypeCode: "",
                canManage: false,
              },
            ],
            pending_memberships: [],
            representative: { username: "alice", full_name: "Alice Example" },
            contact_groups: [],
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

    expect(wrapper.text()).toContain("Expires Apr 30, 2026");
    expect(wrapper.text()).not.toContain("Request renewal");
  });

  it("renders the membership card header actions for a privileged representative", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          organization: {
            id: 1,
            name: "Acme Org",
            status: "active",
            is_representative: true,
            website: "",
            logo_url: "",
            can_view_history: true,
            can_request_any: true,
            can_request_membership: true,
            memberships: [],
            pending_memberships: [],
            representative: { username: "alice", full_name: "Alice Example" },
            contact_groups: [],
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

    const historyLink = wrapper.findAll("a").find((link) => link.text() === "History");
    expect(historyLink?.attributes("href")).toBe("/membership/log/org/1/");
    const requestLink = wrapper.findAll("a").find((link) => link.text() === "Request membership");
    expect(requestLink?.attributes("href")).toBe("/organization/1/membership/request/");
  });

  it("omits the membership card header actions when the backend withholds them", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          organization: {
            id: 1,
            name: "Acme Org",
            status: "active",
            is_representative: false,
            website: "",
            logo_url: "",
            can_view_history: false,
            can_request_any: false,
            can_request_membership: false,
            memberships: [],
            pending_memberships: [],
            representative: { username: "alice", full_name: "Alice Example" },
            contact_groups: [],
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

    expect(wrapper.findAll("a").some((link) => link.text() === "History")).toBe(false);
    expect(wrapper.findAll("a").some((link) => link.text() === "Request membership")).toBe(false);
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
            memberships: [{ requestId: null,
                kind: "membership" as const,
                key: "membership-gold", membershipType: { name: "Gold Sponsor Member", code: "gold", description: "" }, createdAt: null, expiresAt: null, isExpiringSoon: false, canRequestTierChange: false, tierChangeMembershipTypeCode: "", canManage: false }],
            pending_memberships: [],
            representative: { username: "alice", full_name: "Alice Example" },
            contact_groups: [{ key: "business", name: "Business Person", email: "biz@example.com", phone: "" }],
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
              memberships: [{ requestId: null,
                kind: "membership" as const,
                key: "membership-gold", membershipType: { name: "Gold Sponsor Member", code: "gold", description: "" }, createdAt: null, expiresAt: null, isExpiringSoon: false, canRequestTierChange: false, tierChangeMembershipTypeCode: "", canManage: false }],
              pending_memberships: [],
              representative: { username: "alice", full_name: "Alice Example" },
              contact_groups: [{ key: "business", name: "Business Person", email: "biz@example.com", phone: "" }],
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