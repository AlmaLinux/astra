import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

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

describe("InvitationsTable", () => {
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
});
