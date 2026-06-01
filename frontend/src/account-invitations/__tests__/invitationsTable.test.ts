import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import InvitationsTable from "../components/InvitationsTable.vue";
import type { AccountInvitationRow, AccountInvitationsBootstrap } from "../types";

const bootstrap: AccountInvitationsBootstrap = {
  pendingApiUrl: "/api/v1/account/invitations/pending",
  acceptedApiUrl: "/api/v1/account/invitations/accepted",
  refreshApiUrl: "/api/v1/account/invitations/refresh",
  resendApiUrl: "/api/v1/account/invitations/123456789/resend",
  dismissApiUrl: "/api/v1/account/invitations/123456789/dismiss",
  bulkApiUrl: "/api/v1/account/invitations/bulk",
  listPageUrl: "/account/invitations/",
  pageSize: 50,
  canManageInvitations: true,
  canRefresh: true,
  canResend: true,
  canDismiss: true,
  canBulkAction: true,
  sentinelToken: "123456789",
  csrfToken: "csrf-token",
};

const row: AccountInvitationRow = {
  invitation_id: 10,
  email: "alice@example.com",
  full_name: "Alice",
  note: "",
  invited_by_username: "bob",
  invited_at: "2026-04-20T10:00:00",
  send_count: 1,
  last_sent_at: "2026-04-20T10:00:00",
  status: "pending",
  organization_id: 33,
  organization_name: "Example Org",
};

const acceptedRow: AccountInvitationRow = {
  invitation_id: 20,
  email: "accepted@example.com",
  full_name: "Accepted User",
  note: "Accepted note",
  invited_by_username: "bob",
  invited_at: "2026-04-20T10:00:00",
  send_count: 1,
  last_sent_at: "2026-04-20T10:00:00",
  status: "accepted",
  organization_id: 44,
  organization_name: "Accepted Org",
  accepted_at: "2026-04-21T12:00:00",
  accepted_username: "accepteduser",
  freeipa_matched_usernames: ["accepteduser"],
};

function flushPromises(): Promise<void> {
  return Promise.resolve();
}

describe("InvitationsTable", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders pending invitations with tbody-based loading/error states", async () => {
    const wrapper = mount(InvitationsTable, {
      props: {
        bootstrap,
        rows: [],
        count: 0,
        currentPage: 1,
        totalPages: 1,
        isLoading: true,
        error: null,
        scope: "pending",
        buildPageHref: (page: number) => `?page=${page}`,
      },
    });

    expect(wrapper.find("tbody td").text()).toContain("Loading pending invitations...");

    await wrapper.setProps({ isLoading: false, error: "Failed to load invitations." });
    expect(wrapper.find("tbody td").text()).toContain("Failed to load invitations.");
    expect(wrapper.find(".alert.alert-danger").exists()).toBe(false);
  });

  it("uses inline errors instead of alert dialogs for bulk-action validation", async () => {
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);

    const wrapper = mount(InvitationsTable, {
      props: {
        bootstrap,
        rows: [row],
        count: 1,
        currentPage: 1,
        totalPages: 1,
        isLoading: false,
        error: null,
        scope: "pending",
        buildPageHref: (page: number) => `?page=${page}`,
      },
    });

    await wrapper.find("form").trigger("submit");

    expect(alertSpy).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("Please select an action and at least one invitation.");

    alertSpy.mockRestore();
  });

  it("submits the pending bulk action as JSON with scope and selected ids", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(InvitationsTable, {
      props: {
        bootstrap,
        rows: [row],
        count: 1,
        currentPage: 1,
        totalPages: 1,
        isLoading: false,
        error: null,
        scope: "pending",
        buildPageHref: (page: number) => `?page=${page}`,
      },
    });

    await wrapper.get<HTMLInputElement>('tbody input[type="checkbox"][name="selected"][value="10"]').setValue(true);
    await wrapper.get('select[name="bulk_action"]').setValue("resend");
    await wrapper.get("form#bulk-invitations-pending-form").trigger("submit");
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [requestUrl, requestInit] = fetchMock.mock.calls[0] ?? [];
    expect(requestUrl).toBe(bootstrap.bulkApiUrl);
    expect(requestInit).toMatchObject({
      method: "POST",
      credentials: "same-origin",
      headers: expect.objectContaining({
        "X-CSRFToken": bootstrap.csrfToken,
        Accept: "application/json",
        "Content-Type": "application/json",
      }),
    });
    expect(JSON.parse(String(requestInit?.body))).toEqual({
      bulk_action: "resend",
      bulk_scope: "pending",
      selected: [10],
    });
  });

  it("shows selected-count feedback for pending table selections", async () => {
    const wrapper = mount(InvitationsTable, {
      props: {
        bootstrap,
        rows: [row],
        count: 1,
        currentPage: 1,
        totalPages: 1,
        isLoading: false,
        error: null,
        scope: "pending",
        buildPageHref: (page: number) => `?page=${page}`,
      },
    });

    await wrapper.get<HTMLInputElement>('tbody input[type="checkbox"][name="selected"][value="10"]').setValue(true);

    expect(wrapper.text()).toContain("Selected: 1");
  });

  it("shows an inline success message after a resend action succeeds", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, message: "Invitation resent to alice@example.com" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(InvitationsTable, {
      props: {
        bootstrap,
        rows: [row],
        count: 1,
        currentPage: 1,
        totalPages: 1,
        isLoading: false,
        error: null,
        scope: "pending",
        buildPageHref: (page: number) => `?page=${page}`,
      },
    });

    await wrapper.get('form.d-inline').trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain("Invitation resent to alice@example.com");
  });

  it("shows an inline success message after a bulk action succeeds", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, message: "Dismissed 1 invitation(s)" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(InvitationsTable, {
      props: {
        bootstrap,
        rows: [acceptedRow],
        count: 1,
        currentPage: 1,
        totalPages: 1,
        isLoading: false,
        error: null,
        scope: "accepted",
        buildPageHref: (page: number) => `?page=${page}`,
      },
    });

    await wrapper.get<HTMLInputElement>('tbody input[type="checkbox"][name="selected"][value="20"]').setValue(true);
    await wrapper.get('select[name="bulk_action"]').setValue("dismiss");
    await wrapper.get("form#bulk-invitations-accepted-form").trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain("Dismissed 1 invitation(s)");
  });

  it("submits the accepted bulk action as JSON with scope and selected ids", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(InvitationsTable, {
      props: {
        bootstrap,
        rows: [acceptedRow],
        count: 1,
        currentPage: 1,
        totalPages: 1,
        isLoading: false,
        error: null,
        scope: "accepted",
        buildPageHref: (page: number) => `?page=${page}`,
      },
    });

    await wrapper.get<HTMLInputElement>('tbody input[type="checkbox"][name="selected"][value="20"]').setValue(true);
    await wrapper.get('select[name="bulk_action"]').setValue("dismiss");
    await wrapper.get("form#bulk-invitations-accepted-form").trigger("submit");
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [requestUrl, requestInit] = fetchMock.mock.calls[0] ?? [];
    expect(requestUrl).toBe(bootstrap.bulkApiUrl);
    expect(requestInit).toMatchObject({
      method: "POST",
      credentials: "same-origin",
      headers: expect.objectContaining({
        "X-CSRFToken": bootstrap.csrfToken,
        Accept: "application/json",
        "Content-Type": "application/json",
      }),
    });
    expect(JSON.parse(String(requestInit?.body))).toEqual({
      bulk_action: "dismiss",
      bulk_scope: "accepted",
      selected: [20],
    });
  });
});
