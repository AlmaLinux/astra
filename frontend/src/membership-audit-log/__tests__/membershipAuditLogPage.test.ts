import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import MembershipAuditLogPage from "../MembershipAuditLogPage.vue";
import type { MembershipAuditLogBootstrap } from "../types";

function expectedLocalTimestamp(value: string): string {
  const parsed = new Date(value);
  const year = String(parsed.getFullYear());
  const month = String(parsed.getMonth() + 1).padStart(2, "0");
  const day = String(parsed.getDate()).padStart(2, "0");
  const hour = String(parsed.getHours()).padStart(2, "0");
  const minute = String(parsed.getMinutes()).padStart(2, "0");
  const timezoneOffsetMinutes = -parsed.getTimezoneOffset();
  const offsetSign = timezoneOffsetMinutes >= 0 ? "+" : "-";
  const absoluteOffsetMinutes = Math.abs(timezoneOffsetMinutes);
  const offsetHours = String(Math.floor(absoluteOffsetMinutes / 60)).padStart(2, "0");
  const offsetMinutes = String(absoluteOffsetMinutes % 60).padStart(2, "0");

  return `${year}-${month}-${day} ${hour}:${minute} UTC${offsetSign}${offsetHours}:${offsetMinutes}`;
}

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const bootstrap: MembershipAuditLogBootstrap = {
  apiUrl: "/api/v1/membership/audit-log/detail",
  pageSize: 50,
  initialQ: "",
  initialUsername: "",
  initialOrganization: "",
  userProfileUrlTemplate: "/user/__username__/",
  organizationDetailUrlTemplate: "/organization/__organization_id__/",
  membershipRequestDetailUrlTemplate: "/membership/request/__request_id__/",
};

describe("MembershipAuditLogPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads and renders audit log rows with request response details", async () => {
    const createdAt = "2026-04-23T12:00:00+00:00";
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          draw: 1,
          recordsTotal: 1,
          recordsFiltered: 1,
          data: [
            {
              log_id: 10,
              created_at: createdAt,
              actor_username: "reviewer",
              target: {
                kind: "user",
                id: null,
                label: "alice",
                secondary_label: "",
                deleted: false,
              },
              membership_name: "Individual",
              action: "requested",
              expires_at: null,
              request: {
                request_id: 7,
                responses: [
                  {
                    question: "Contributions",
                    answer_text: "Patch submissions",
                    segments: [{ kind: "text", text: "Patch submissions" }],
                  },
                ],
              },
            },
          ],
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipAuditLogPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalled();
    expect(wrapper.text()).toContain("reviewer");
    expect(wrapper.text()).toContain("alice");
    expect(wrapper.text()).toContain("Individual");
    expect(wrapper.text()).toContain("Requested");
    expect(wrapper.text()).toContain(expectedLocalTimestamp(createdAt));
    expect(wrapper.text()).not.toContain("Thu, 23 Apr 2026 12:00:00 +0000");
    expect(wrapper.text()).toContain("Request responses");
    expect(wrapper.text()).toContain("Contributions");
    expect(wrapper.text()).toContain("Patch submissions");
    expect(wrapper.find('a[href="/membership/request/7/"]').exists()).toBe(true);
  });

  it("labels dues changes and links them to the request of the term they change", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      draw: 1,
      recordsTotal: 1,
      recordsFiltered: 1,
      data: [{
        log_id: 11,
        created_at: "2026-10-15T19:08:00+00:00",
        actor_username: "committee",
        target: { kind: "organization", id: 3, label: "CERN", secondary_label: "", deleted: false },
        membership_name: "Platinum Sponsor",
        action: "dues_changed",
        expires_at: "2027-01-12T23:59:59+00:00",
        request: { request_id: 194, responses: [] },
      }],
    }))));

    const wrapper = mount(MembershipAuditLogPage, { props: { bootstrap } });
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Dues changed");
    expect(wrapper.find('a[href="/membership/request/194/"]').exists()).toBe(true);
  });

  it("labels invoice renders and links the stored PDF", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      draw: 1,
      recordsTotal: 2,
      recordsFiltered: 2,
      data: [
        {
          log_id: 13,
          created_at: "2026-10-18T10:00:00+00:00",
          actor_username: "rep1",
          target: { kind: "organization", id: 3, label: "CERN", secondary_label: "", deleted: false },
          membership_name: "Platinum Sponsor",
          action: "invoice_regenerated",
          expires_at: null,
          request: { request_id: 194, responses: [] },
          invoice: { number: "0003-2027-370275", url: "https://files.example/invoices/tok/inv.pdf" },
        },
        {
          log_id: 12,
          created_at: "2026-10-17T10:00:00+00:00",
          actor_username: "-",
          target: { kind: "organization", id: null, label: "Gone Corp", secondary_label: "", deleted: true },
          membership_name: "Platinum Sponsor",
          action: "invoice_generated",
          expires_at: null,
          request: null,
          // The organization was deleted, so its PDF can no longer be reached.
          invoice: { number: "0004-2026-111111", url: null },
        },
      ],
    }))));

    const wrapper = mount(MembershipAuditLogPage, { props: { bootstrap } });
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Invoice regenerated");
    expect(wrapper.text()).toContain("Invoice generated");
    const link = wrapper.find('a[href="https://files.example/invoices/tok/inv.pdf"]');
    expect(link.exists()).toBe(true);
    expect(link.text()).toContain("0003-2027-370275");
    expect(wrapper.text()).toContain("0004-2026-111111");
  });

  it("shows who a representative change replaced", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      draw: 1,
      recordsTotal: 1,
      recordsFiltered: 1,
      data: [{
        log_id: 14,
        created_at: "2026-10-18T10:00:00+00:00",
        actor_username: "committee",
        target: { kind: "organization", id: 3, label: "CERN", secondary_label: "", deleted: false },
        membership_name: "Platinum Sponsor",
        action: "representative_changed",
        expires_at: null,
        request: null,
        invoice: null,
        representative: { old: "carol", new: "bob" },
      }],
    }))));

    const wrapper = mount(MembershipAuditLogPage, { props: { bootstrap } });
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Representative changed");
    expect(wrapper.text()).toContain("carol → bob");
  });

  it("renders canonical request-response link segments as anchors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        return new Response(
          JSON.stringify({
            draw: 1,
            recordsTotal: 1,
            recordsFiltered: 1,
            data: [
              {
                log_id: 11,
                created_at: "2026-04-23T12:00:00+00:00",
                actor_username: "reviewer",
                target: {
                  kind: "user",
                  id: null,
                  label: "alice",
                  secondary_label: "",
                  deleted: false,
                },
                membership_name: "Individual",
                action: "requested",
                expires_at: null,
                request: {
                  request_id: 8,
                  responses: [
                    {
                      question: "Portfolio",
                      answer_text: "Profile: https://example.com/work",
                      segments: [
                        { kind: "text", text: "Profile: " },
                        { kind: "link", text: "https://example.com/work", url: "https://example.com/work" },
                      ],
                    },
                  ],
                },
              },
            ],
          }),
        );
      }),
    );

    const wrapper = mount(MembershipAuditLogPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    const responseLink = wrapper.find('a[href="https://example.com/work"]');
    expect(responseLink.exists()).toBe(true);
    expect(responseLink.text()).toBe("https://example.com/work");
    expect(wrapper.text()).toContain("Profile:");
  });

  it("sends q to API and keeps username/organization filters in page URL", async () => {
    const historySpy = vi.spyOn(window.history, "replaceState");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        return new Response(
          JSON.stringify({
            draw: 1,
            recordsTotal: 0,
            recordsFiltered: 0,
            data: [],
            echoedUrl: url,
          }),
        );
      }),
    );

    const wrapper = mount(MembershipAuditLogPage, {
      props: {
        bootstrap: {
          ...bootstrap,
          initialUsername: "alice",
          initialOrganization: "42",
        },
      },
    });

    await flushPromises();
    await flushPromises();

    await wrapper.get('input[name="q"]').setValue("reviewer");
    await wrapper.get('form[data-audit-log-search-form]').trigger("submit");
    await flushPromises();
    await flushPromises();

    const fetchCalls = (global.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map(([url]) => String(url));
    expect(fetchCalls.some((url) => url.includes("q=reviewer"))).toBe(true);
    expect(historySpy).toHaveBeenCalled();
    const lastCall = historySpy.mock.calls.at(-1);
    expect(String(lastCall?.[2])).toContain("q=reviewer");
    expect(String(lastCall?.[2])).toContain("username=alice");
    expect(String(lastCall?.[2])).toContain("organization=42");
  });

  it("shows clear button when q exists and clears search while preserving filters", async () => {
    const historySpy = vi.spyOn(window.history, "replaceState");
    window.history.replaceState(null, "", "/membership/log/?q=alice&username=alice&organization=42");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      return new Response(
        JSON.stringify({
          draw: 1,
          recordsTotal: 0,
          recordsFiltered: 0,
          data: [],
          echoedUrl: url,
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipAuditLogPage, {
      props: {
        bootstrap: {
          ...bootstrap,
          initialQ: "alice",
          initialUsername: "alice",
          initialOrganization: "42",
        },
      },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.find('button[aria-label="Clear search"]').exists()).toBe(true);

    await wrapper.get('button[aria-label="Clear search"]').trigger("click");
    await flushPromises();
    await flushPromises();

    const fetchCalls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(fetchCalls.some((url) => url.includes("q=alice"))).toBe(true);
    expect(fetchCalls.some((url) => !url.includes("q=alice") && url.includes("username=alice") && url.includes("organization=42"))).toBe(true);

    const lastCall = historySpy.mock.calls.at(-1);
    expect(String(lastCall?.[2])).not.toContain("q=alice");
    expect(String(lastCall?.[2])).toContain("username=alice");
    expect(String(lastCall?.[2])).toContain("organization=42");
  });
});
