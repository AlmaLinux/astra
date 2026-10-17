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
  membershipUpdateUrlTemplate: "/membership/__membership_id__/update/",
  membershipTerminateUrlTemplate: "/membership/__membership_id__/terminate/",
  sponsorshipInvoiceRegenerateUrlTemplate: "/organization/1/sponsorship/__membership_type_code__/invoice/regenerate/",
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
    expect(wrapper.text()).toContain("Member since January 15 2024");
    expect(wrapper.text()).toContain("Expires April 30 2026");
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
                id: 7,
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
    expect(wrapper.text()).toContain("Edit membership");
    expect(wrapper.text()).not.toContain("Edit expiration");
    expect(wrapper.find('form[action="/membership/7/update/"]').exists()).toBe(true);
    const expiryReason = wrapper.get('form[action="/membership/7/update/"] textarea[name="expiration_reason"]');
    expect(expiryReason.attributes("required")).toBeUndefined();
    expect(wrapper.find('form[action="/membership/7/terminate/"]').exists()).toBe(true);
    const terminate = wrapper.get("#sponsorship-expiry-modal-gold-terminate-submit");
    expect(terminate.attributes("disabled")).toBeDefined();
    expect(terminate.element.parentElement?.getAttribute("title")).toBe("Type the organization name to confirm first.");
    await wrapper.get("#sponsorship-expiry-modal-gold-terminate-confirm-input").setValue("acme org");
    expect(terminate.attributes("disabled")).toBeUndefined();
    expect(terminate.element.parentElement?.getAttribute("title")).toBe("Terminate this membership now");
    expect((wrapper.get("#sponsorship-expiry-modal-gold-expires-on").element as HTMLInputElement).value).toBe("2026-04-30");
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

    expect(wrapper.text()).toContain("Expires April 30 2026");
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
                key: "membership-gold", membershipType: { name: "Gold Sponsor Member", code: "gold", description: "" }, createdAt: null, expiresAt: "2027-01-15T12:00:00Z", isExpiringSoon: false, canRequestTierChange: false, tierChangeMembershipTypeCode: "", canManage: false }],
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
                key: "membership-gold", membershipType: { name: "Gold Sponsor Member", code: "gold", description: "" }, createdAt: null, expiresAt: "2027-01-15T12:00:00Z", isExpiringSoon: false, canRequestTierChange: false, tierChangeMembershipTypeCode: "", canManage: false }],
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

  function mountWithOrganization(organization: Record<string, unknown>) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({
        organization: {
          id: 1,
          name: "Acme Org",
          status: "active",
          website: "",
          logo_url: "",
          pending_memberships: [],
          representative: { username: "alice", full_name: "Alice Example" },
          contact_groups: [],
          address: { street: "", city: "", state: "", postal_code: "", country_code: "" },
          billing_notes: "",
          ...organization,
        },
      }))),
    );
    return mount(OrganizationDetailPage, {
      props: { bootstrap },
      global: {
        stubs: {
          MembershipNotesCard: { template: "<div />" },
        },
      },
    });
  }

  function goldMembership(invoice: { number: string; url: string | null } | null) {
    return {
      requestId: null,
      kind: "membership" as const,
      key: "membership-gold",
      id: 7,
      membershipType: { name: "Gold Sponsor Member", code: "gold", description: "", annualDuesUsd: 20000 },
      createdAt: null,
      expiresAt: "2026-11-18T23:59:59Z",
      isExpiringSoon: true,
      canRenew: false,
      renewalMembershipTypeCode: "",
      canRequestTierChange: false,
      tierChangeMembershipTypeCode: "",
      canManage: false,
      invoice,
      duesUsd: 20000,
    };
  }

  it("shows a sponsor's annual dues next to the membership", async () => {
    for (const [duesUsd, expected] of [
      [20000, "Annual dues: $20,000"],
      [null, "Annual dues: $20,000"],
      [5000, "Annual dues: $5,000 (discounted from $20,000)"],
      [0, "In-kind sponsor: dues waived"],
    ] as const) {
      const wrapper = mountWithOrganization({ memberships: [{ ...goldMembership(null), duesUsd }] });
      await flushPromises();
      await flushPromises();
      expect(wrapper.find('[data-membership-dues="gold"]').text()).toBe(expected);
    }
  });

  it("states the dues once, on their own line, instead of in the tier description", async () => {
    const wrapper = mountWithOrganization({
      memberships: [{
        ...goldMembership(null),
        membershipType: { name: "Gold Sponsor Member", code: "gold", description: "Gold tier", annualDuesUsd: 20000 },
      }],
      pending_memberships: [{
        requestId: 9,
        kind: "pending",
        key: "pending-9",
        status: "pending",
        membershipType: { name: "Platinum Sponsor Member", code: "platinum", description: "Platinum tier", annualDuesUsd: 100000 },
      }],
    });

    await flushPromises();
    await flushPromises();

    const rows = wrapper.findAll("li.list-group-item");
    const active = rows.find((item) => item.text().includes("Gold Sponsor Member"))!;
    const pending = rows.find((item) => item.text().includes("Platinum Sponsor Member"))!;
    expect(active.text()).toContain("Gold tier");
    expect(pending.text()).toContain("Platinum tier");
    expect(pending.text()).toContain("Annual dues: $100,000");
    for (const row of [active, pending]) {
      expect(row.text()).not.toContain("USD");
      expect(row.text().split("Annual dues").length - 1).toBe(1);
    }
  });

  it("lets the committee change a sponsor's dues alongside the expiration in the manage dialog", async () => {
    const wrapper = mountWithOrganization({ memberships: [{ ...goldMembership(null), canManage: true, duesUsd: 5000 }] });

    await flushPromises();
    await flushPromises();

    const form = wrapper.get('#sponsorship-expiry-modal-gold form[action="/membership/7/update/"]');
    expect((form.get('input[name="expires_on"]').element as HTMLInputElement).value).toBe("2026-11-18");
    expect((form.get('input[name="dues_usd"]').element as HTMLInputElement).value).toBe("5000");
    expect(form.text()).toContain("Leave empty to charge the standard price of $20,000");
  });

  it("names the membership once, without repeating it as a badge", async () => {
    const wrapper = mountWithOrganization({ memberships: [goldMembership(null)] });

    await flushPromises();
    await flushPromises();

    const [row] = wrapper.findAll("li.list-group-item").filter((item) => item.text().includes("Gold Sponsor Member"));
    expect(row.text().split("Gold Sponsor Member").length - 1).toBe(1);
    expect(row.find(".badge").exists()).toBe(false);
    // Nothing for the viewer to do, so no empty action row either.
    expect(row.find('[data-membership-actions="representative"]').exists()).toBe(false);
  });

  it("shows representatives their discount without any way to change it", async () => {
    const wrapper = mountWithOrganization({ memberships: [{ ...goldMembership(null), canManage: false, duesUsd: 5000 }] });

    await flushPromises();
    await flushPromises();

    expect(wrapper.find('[data-membership-dues="gold"]').text()).toBe("Annual dues: $5,000 (discounted from $20,000)");
    expect(wrapper.find('input[name="dues_usd"]').exists()).toBe(false);
    expect(wrapper.find('form[action="/membership/7/update/"]').exists()).toBe(false);
  });

  it("has no dues form for tiers without dues", async () => {
    const wrapper = mountWithOrganization({
      memberships: [{
        ...goldMembership(null),
        canManage: true,
        membershipType: { name: "Community", code: "community", description: "", annualDuesUsd: 0 },
        duesUsd: 0,
      }],
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.find('input[name="dues_usd"]').exists()).toBe(false);
    expect(wrapper.find('[data-membership-dues="community"]').exists()).toBe(false);
  });

  it("renders the invoice download link and a confirmed regenerate form", async () => {
    const wrapper = mountWithOrganization({
      memberships: [goldMembership({ number: "0001-2026-318305", url: "https://static.example.com/invoices/tok/AlmaLinux_OS_Foundation_Invoice_0001-2026-318305.pdf" })],
    });

    await flushPromises();
    await flushPromises();

    const download = wrapper.find('a[href="https://static.example.com/invoices/tok/AlmaLinux_OS_Foundation_Invoice_0001-2026-318305.pdf"]');
    expect(download.exists()).toBe(true);
    expect(download.text()).toBe("Download invoice 0001-2026-318305");

    const trigger = wrapper.find('button[data-target="#sponsorship-invoice-modal-gold"]');
    expect(trigger.text()).toBe("Regenerate invoice");
    const form = wrapper.find('#sponsorship-invoice-modal-gold form[action="/organization/1/sponsorship/gold/invoice/regenerate/"]');
    expect(form.exists()).toBe(true);
    expect(form.attributes("method")).toBe("post");
    expect(form.find('input[name="csrfmiddlewaretoken"]').attributes("value")).toBe("csrf-token");
  });

  it("offers to generate the invoice when none is stored yet", async () => {
    const wrapper = mountWithOrganization({ memberships: [goldMembership({ number: "0001-2026-318305", url: null })] });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).not.toContain("Download invoice");
    expect(wrapper.text()).not.toContain("0001-2026-318305");
    expect(wrapper.find('button[data-target="#sponsorship-invoice-modal-gold"]').text()).toBe("Generate invoice");
  });

  it("omits invoice controls when the backend withholds the invoice", async () => {
    const wrapper = mountWithOrganization({ memberships: [goldMembership(null)] });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).not.toContain("invoice");
    expect(wrapper.find("#sponsorship-invoice-modal-gold").exists()).toBe(false);
  });

  it("renders billing notes with the organization information", async () => {
    const wrapper = mountWithOrganization({ memberships: [], billing_notes: "PO: 70001165892" });

    await flushPromises();
    await flushPromises();

    expect(wrapper.find(".organization-info-card").text()).toContain("PO: 70001165892");
  });

  it("groups representative actions above committee-only actions", async () => {
    const wrapper = mountWithOrganization({
      memberships: [{
        ...goldMembership({ number: "0001-2026-318305", url: null }),
        canRenew: true,
        renewalMembershipTypeCode: "gold",
        canRequestTierChange: true,
        tierChangeMembershipTypeCode: "platinum",
        canManage: true,
      }],
    });

    await flushPromises();
    await flushPromises();

    const representativeRow = wrapper.find('[data-membership-actions="representative"]');
    const committeeRow = wrapper.find('[data-membership-actions="committee"]');
    expect(representativeRow.text()).toContain("Request renewal");
    expect(representativeRow.text()).toContain("Change tier");
    expect(representativeRow.text()).toContain("Generate invoice");
    expect(representativeRow.text()).not.toContain("Edit membership");
    expect(committeeRow.text()).toBe("Edit membership");
  });

  it("omits the committee action row for viewers who cannot manage the membership", async () => {
    const wrapper = mountWithOrganization({
      memberships: [{ ...goldMembership({ number: "0001-2026-318305", url: null }), canRenew: true, renewalMembershipTypeCode: "gold" }],
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.find('[data-membership-actions="representative"]').text()).toContain("Request renewal");
    expect(wrapper.find('[data-membership-actions="committee"]').exists()).toBe(false);
  });
});
