import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { defineComponent, nextTick } from "vue";

import AccountInvitationsPage from "../AccountInvitationsPage.vue";
import type { AccountInvitationsBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const bootstrap: AccountInvitationsBootstrap = {
  pendingApiUrl: "/api/v1/membership/invitations/pending/detail",
  acceptedApiUrl: "/api/v1/membership/invitations/accepted/detail",
  refreshApiUrl: "/api/v1/membership/invitations/refresh",
  resendApiUrl: "/api/v1/membership/invitations/123456789/resend",
  dismissApiUrl: "/api/v1/membership/invitations/123456789/dismiss",
  bulkApiUrl: "/api/v1/membership/invitations/bulk",
  listPageUrl: "/membership/invitations/",
  uploadPageUrl: "/membership/invitations/upload/",
  pageSize: 25,
  canManageInvitations: true,
  canRefresh: true,
  canResend: true,
  canDismiss: true,
  canBulkAction: true,
  sentinelToken: "123456789",
  csrfToken: "csrf-token",
};

const InvitationsTableStub = defineComponent({
  name: "InvitationsTable",
  props: {
    scope: { type: String, required: true },
  },
  template: '<button type="button" :data-scope="scope" @click="$emit(\'page-change\', scope === \'accepted\' ? 3 : 2)">{{ scope }}</button>',
});

describe("AccountInvitationsPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("writes pending_page and accepted_page back to the URL while preserving other params", async () => {
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
    window.history.replaceState(null, "", "/membership/invitations/?filter=needs-review&accepted_page=4");

    const wrapper = mount(AccountInvitationsPage, {
      props: { bootstrap },
      global: {
        stubs: {
          InvitationsTable: InvitationsTableStub,
        },
      },
    });

    await flushPromises();
    await flushPromises();

    const buttons = wrapper.findAll("button[data-scope]");
    await buttons[1].trigger("click");
    await nextTick();
    await flushPromises();

    let lastCall = historySpy.mock.calls.at(-1);
    expect(lastCall).toBeDefined();
    expect(String(lastCall?.[2])).toContain("filter=needs-review");
    expect(String(lastCall?.[2])).toContain("pending_page=2");
    expect(String(lastCall?.[2])).toContain("accepted_page=4");

    await buttons[0].trigger("click");
    await nextTick();
    await flushPromises();

    lastCall = historySpy.mock.calls.at(-1);
    expect(String(lastCall?.[2])).toContain("filter=needs-review");
    expect(String(lastCall?.[2])).toContain("pending_page=2");
    expect(String(lastCall?.[2])).toContain("accepted_page=3");
  });

  it("restores pending_page and accepted_page from the URL on mount", async () => {
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
    window.history.replaceState(null, "", "/membership/invitations/?pending_page=2&accepted_page=4");

    mount(AccountInvitationsPage, {
      props: { bootstrap },
      global: {
        stubs: {
          InvitationsTable: InvitationsTableStub,
        },
      },
    });

    await flushPromises();
    await flushPromises();

    const urls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(urls.some((url) => url.includes("/pending/detail") && url.includes("start=25") && url.includes("draw=2"))).toBe(true);
    expect(urls.some((url) => url.includes("/accepted/detail") && url.includes("start=75") && url.includes("draw=4"))).toBe(true);
  });
});