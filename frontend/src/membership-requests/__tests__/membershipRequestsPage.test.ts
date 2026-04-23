import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import MembershipRequestsPage from "../MembershipRequestsPage.vue";
import type { MembershipRequestsBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const bootstrap: MembershipRequestsBootstrap = {
  clearFilterUrl: "/membership/requests/",
  pendingApiUrl: "/api/v1/membership/requests/pending",
  onHoldApiUrl: "/api/v1/membership/requests/on-hold",
  pendingPageSize: 25,
  onHoldPageSize: 10,
  requestIdSentinel: "123456789",
  requestDetailTemplate: "/membership/request/123456789/",
  approveTemplate: "/membership/requests/123456789/approve/",
  approveOnHoldTemplate: "/membership/requests/123456789/approve-on-hold/",
  rejectTemplate: "/membership/requests/123456789/reject/",
  requestInfoTemplate: "/membership/requests/123456789/rfi/",
  ignoreTemplate: "/membership/requests/123456789/ignore/",
  noteAddTemplate: "/membership/requests/123456789/notes/add/",
  noteSummaryTemplate: "/api/v1/membership/notes/123456789/summary",
  noteDetailTemplate: "/api/v1/membership/notes/123456789",
  userProfileTemplate: "/user/__username__/",
  organizationDetailTemplate: "/organization/123456789/",
  canRequestInfo: true,
  notesCanView: true,
  notesCanWrite: true,
  notesCanVote: true,
};

describe("MembershipRequestsPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads pending and on-hold tables from the existing queue endpoints", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/v1/membership/requests/pending")) {
        return new Response(
          JSON.stringify({
            draw: 1,
            recordsTotal: 1,
            recordsFiltered: 1,
            pending_filter: {
              selected: "all",
              options: [
                { value: "all", label: "All", count: 1 },
                { value: "renewals", label: "Renewals", count: 0 },
              ],
            },
            data: [
              {
                request_id: 10,
                status: "pending",
                requested_at: "2026-04-21T12:00:00+00:00",
                on_hold_since: null,
                target: {
                  kind: "user",
                  label: "Alice Example",
                  secondary_label: "alice",
                  username: "alice",
                  deleted: false,
                },
                requested_by: {
                  show: true,
                  username: "bob",
                  full_name: "Bob Reviewer",
                  deleted: false,
                },
                membership_type: {
                  id: "individual",
                  code: "individual",
                  name: "Individual",
                  category: "individual",
                },
                is_renewal: false,
                responses: [],
              },
            ],
          }),
        );
      }

      return new Response(
        JSON.stringify({
          draw: 1,
          recordsTotal: 1,
          recordsFiltered: 1,
          data: [
            {
              request_id: 11,
              status: "on_hold",
              requested_at: "2026-04-20T12:00:00+00:00",
              on_hold_since: "2026-04-20T12:00:00+00:00",
              target: {
                kind: "organization",
                label: "Acme Org",
                secondary_label: "sponsor@example.com",
                organization_id: 42,
                deleted: false,
              },
              requested_by: {
                show: true,
                username: "bob",
                full_name: "Bob Reviewer",
                deleted: false,
              },
              membership_type: {
                id: "sponsor",
                code: "sponsor",
                name: "Sponsor",
                category: "sponsorship",
              },
              is_renewal: true,
              responses: [],
            },
          ],
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipRequestsPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(fetchMock.mock.calls[0]?.[0]).toContain("queue_filter=all");
    expect(fetchMock.mock.calls[0]?.[0]).toContain("length=25");
    expect(fetchMock.mock.calls[1]?.[0]).toContain("length=10");
    expect(wrapper.text()).toContain("Pending: 1");
    expect(wrapper.text()).toContain("On hold: 1");
    expect(wrapper.text()).toContain("Alice Example");
    expect(wrapper.text()).toContain("Acme Org");
    expect(wrapper.text()).toContain("Requested by: Bob Reviewer (bob)");
    expect(wrapper.text()).toContain("sponsor@example.com");
    expect(wrapper.text()).toContain("Approve");
    expect(wrapper.text()).toContain("Reject");
    expect(wrapper.find('a[href="/organization/42/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/user/bob/"]').exists()).toBe(true);
    expect(wrapper.find('input.request-checkbox--pending').exists()).toBe(true);
    expect(wrapper.find('input.request-checkbox--on-hold').exists()).toBe(true);
  });

  it("renders inline table errors instead of failing the route when queue loads fail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const label = url.includes("pending") ? "pending" : "on-hold";
        return new Response(JSON.stringify({ error: `Failed to load ${label}.` }), { status: 503 });
      }),
    );

    const wrapper = mount(MembershipRequestsPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Failed to load membership requests.");
    expect(wrapper.text()).toContain("Waiting for requester response");
  });

  it("refetches the pending table when the queue filter changes", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/v1/membership/requests/pending") && url.includes("queue_filter=renewals")) {
        return new Response(
          JSON.stringify({
            draw: 1,
            recordsTotal: 0,
            recordsFiltered: 0,
            pending_filter: {
              selected: "renewals",
              options: [
                { value: "all", label: "All", count: 1 },
                { value: "renewals", label: "Renewals", count: 0 },
              ],
            },
            data: [],
          }),
        );
      }

      if (url.startsWith("/api/v1/membership/requests/pending")) {
        return new Response(
          JSON.stringify({
            draw: 1,
            recordsTotal: 1,
            recordsFiltered: 1,
            pending_filter: {
              selected: "all",
              options: [
                { value: "all", label: "All", count: 1 },
                { value: "renewals", label: "Renewals", count: 0 },
              ],
            },
            data: [],
          }),
        );
      }

      return new Response(
        JSON.stringify({ draw: 1, recordsTotal: 0, recordsFiltered: 0, data: [] }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipRequestsPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    await wrapper.get('select[name="filter"]').setValue("renewals");
    await flushPromises();
    await flushPromises();

    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("queue_filter=renewals"))).toBe(true);
  });
});