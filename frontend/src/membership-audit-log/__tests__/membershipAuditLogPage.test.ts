import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import MembershipAuditLogPage from "../MembershipAuditLogPage.vue";
import type { MembershipAuditLogBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const bootstrap: MembershipAuditLogBootstrap = {
  apiUrl: "/api/v1/membership/audit-log",
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
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          draw: 1,
          recordsTotal: 1,
          recordsFiltered: 1,
          data: [
            {
              log_id: 10,
              created_at_display: "Thu, 23 Apr 2026 12:00:00 +0000",
              created_at_iso: "2026-04-23T12:00:00+00:00",
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
              action_display: "Requested",
              expires_display: "",
              request: {
                request_id: 7,
                responses: [
                  {
                    question: "Contributions",
                    answer_html: "Patch submissions",
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
    expect(wrapper.text()).toContain("Request responses");
    expect(wrapper.text()).toContain("Contributions");
    expect(wrapper.text()).toContain("Patch submissions");
    expect(wrapper.find('a[href="/membership/request/7/"]').exists()).toBe(true);
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
