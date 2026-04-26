import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import MembershipRequestDetailPage from "../MembershipRequestDetailPage.vue";
import type { MembershipRequestDetailBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const bootstrap: MembershipRequestDetailBootstrap = {
  apiUrl: "/api/v1/membership/requests/42/detail",
  csrfToken: "csrf-token",
  pageTitle: "Membership Request #42",
  backLinkUrl: "/membership/requests/",
  backLinkLabel: "Back to requests",
  userProfileUrlTemplate: "/user/__username__/",
  organizationDetailUrlTemplate: "/organization/__organization_id__/",
  contactUrl: "/email-tools/send-mail/?to=bob",
  reopenUrl: "/api/v1/membership/request/42/reopen",
  noteSummaryUrl: "/api/v1/membership/notes/42/summary",
  noteDetailUrl: "/api/v1/membership/notes/42",
  noteAddUrl: "/api/v1/membership/notes/42/add",
  noteNextUrl: "/membership/request/42/",
  notesCanView: true,
  notesCanWrite: true,
  notesCanVote: true,
  approveUrl: "/api/v1/membership/request/42/approve",
  approveOnHoldUrl: "/api/v1/membership/request/42/approve-on-hold",
  rejectUrl: "/api/v1/membership/request/42/reject",
  rfiUrl: "/api/v1/membership/request/42/rfi",
  ignoreUrl: "/api/v1/membership/request/42/ignore",
  rescindUrl: "/membership/request/42/rescind/",
  formActionUrl: "/membership/request/42/",
};

describe("MembershipRequestDetailPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("posts to the reopen endpoint with CSRF, refetches detail, and renders warning/deleted markers", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            viewer: {
              mode: "committee",
            },
            request: {
              id: 42,
              status: "ignored",
              requested_at: "2026-04-26T10:00:00+00:00",
              requested_by: { show: true, username: "alice", full_name: "Alice Example", deleted: true },
              requested_for: { show: true, kind: "user", label: "Bob Example", username: "bob", organization_id: null, deleted: true },
              membership_type: { name: "Mirror" },
              responses: [],
            },
            committee: {
              reopen: { show: true },
              compliance_warning: {
                country_code: "IR",
                country_label: "Iran",
                message: "This request matches the embargoed country list: Iran (IR).",
              },
              actions: {
                canRequestInfo: true,
                showOnHoldApprove: false,
              },
            },
          }),
        ),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true })))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            viewer: {
              mode: "committee",
            },
            request: {
              id: 42,
              status: "pending",
              requested_at: "2026-04-26T10:00:00+00:00",
              requested_by: { show: true, username: "alice", full_name: "Alice Example", deleted: true },
              requested_for: { show: true, kind: "user", label: "Bob Example", username: "bob", organization_id: null, deleted: true },
              membership_type: { name: "Mirror" },
              responses: [],
            },
            committee: {
              reopen: { show: false },
              compliance_warning: {
                country_code: "IR",
                country_label: "Iran",
                message: "This request matches the embargoed country list: Iran (IR).",
              },
              actions: {
                canRequestInfo: true,
                showOnHoldApprove: false,
              },
            },
          }),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipRequestDetailPage, {
      props: { bootstrap },
      global: {
        stubs: {
          MembershipNotesCard: {
            template: '<div data-test="notes-card"></div>',
          },
          MembershipRequestDetailActions: {
            template: '<div data-test="actions-card"></div>',
          },
        },
      },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Compliance warning:");
    expect(wrapper.text()).toContain("This request matches the embargoed country list: Iran (IR).");
    expect(wrapper.text()).toContain("(deleted)");

    await wrapper.get('[data-test="reopen-request"]').trigger("click");
    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/v1/membership/request/42/reopen",
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
        headers: expect.objectContaining({
          "X-CSRFToken": "csrf-token",
        }),
      }),
    );
    expect(wrapper.text()).toContain("Pending");
  });

  it("surfaces an error when reopen fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            viewer: {
              mode: "committee",
            },
            request: {
              id: 42,
              status: "ignored",
              requested_at: "2026-04-26T10:00:00+00:00",
              requested_by: { show: false, username: "", full_name: "", deleted: false },
              requested_for: { show: false, kind: "user", label: "", username: "", organization_id: null, deleted: false },
              membership_type: { name: "Mirror" },
              responses: [],
            },
            committee: {
              reopen: { show: true },
              actions: {
                canRequestInfo: false,
                showOnHoldApprove: false,
              },
            },
          }),
        ),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: false }), { status: 500 }));
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipRequestDetailPage, {
      props: { bootstrap },
      global: {
        stubs: {
          MembershipRequestDetailActions: { template: '<div></div>' },
        },
      },
    });

    await flushPromises();
    await flushPromises();

    await wrapper.get('[data-test="reopen-request"]').trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Unable to reopen membership request right now.");
  });

  it("renders the committee detail surface from the read API and refetches after a committee action", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            viewer: {
              mode: "committee",
            },
            request: {
              id: 42,
              status: "ignored",
              requested_at: "2026-04-26T10:00:00+00:00",
              requested_by: { show: true, username: "alice", full_name: "Alice Example", deleted: false },
              requested_for: { show: true, kind: "user", label: "Bob Example", username: "bob", organization_id: null, deleted: false },
              membership_type: { name: "Mirror" },
              responses: [
                {
                  question: "Domain",
                  answer_text: "mirror.example.org",
                  segments: [{ kind: "link", text: "mirror.example.org", url: "https://mirror.example.org" }],
                },
              ],
            },
            committee: {
              reopen: { show: true },
              actions: {
                canRequestInfo: true,
                showOnHoldApprove: false,
              },
            },
          }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            viewer: {
              mode: "committee",
            },
            request: {
              id: 42,
              status: "pending",
              requested_at: "2026-04-26T10:00:00+00:00",
              requested_by: { show: true, username: "alice", full_name: "Alice Example", deleted: false },
              requested_for: { show: true, kind: "user", label: "Bob Example", username: "bob", organization_id: null, deleted: false },
              membership_type: { name: "Mirror" },
              responses: [],
            },
            committee: {
              reopen: { show: false },
              actions: {
                canRequestInfo: true,
                showOnHoldApprove: false,
              },
            },
          }),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipRequestDetailPage, {
      props: { bootstrap },
      global: {
        stubs: {
          MembershipNotesCard: {
            template: '<div data-test="notes-card"></div>',
          },
          MembershipRequestDetailActions: {
            template: '<button data-test="action-success" @click="$emit(\'action-success\', { actionKind: \'ignore\' })">action</button>',
          },
        },
      },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Membership Request #42");
    expect(wrapper.text()).toContain("Ignored");
    expect(wrapper.text()).toContain("Alice Example");
    expect(wrapper.text()).toContain("Bob Example");
    expect(wrapper.find('a[href="https://mirror.example.org"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="notes-card"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("Reopen");

    await wrapper.get('[data-test="action-success"]').trigger("click");
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("uses shell bootstrap for links, labels, note wiring, and action URLs instead of API route fields", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          viewer: {
            mode: "committee",
          },
          request: {
            id: 42,
            status: "pending",
            requested_at: "2026-04-26T10:00:00+00:00",
            requested_by: { show: true, username: "alice", full_name: "Alice Example", deleted: false },
            requested_for: { show: true, kind: "organization", label: "Example Org", username: "", organization_id: 7, deleted: false },
            membership_type: { name: "Mirror" },
            responses: [],
          },
          committee: {
            reopen: { show: true },
            actions: {
              canRequestInfo: true,
              showOnHoldApprove: false,
            },
          },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipRequestDetailPage, {
      props: { bootstrap },
      global: {
        stubs: {
          MembershipNotesCard: {
            template: '<div data-test="notes-card" :data-summary-url="summaryUrl" :data-detail-url="detailUrl" :data-add-url="addUrl" :data-next-url="nextUrl"></div>',
            props: ["requestId", "summaryUrl", "detailUrl", "addUrl", "csrfToken", "nextUrl", "canView", "canWrite", "canVote"],
          },
          MembershipRequestDetailActions: {
            template: '<div data-test="actions-card" :data-approve-url="approveUrl" :data-reject-url="rejectUrl" :data-rfi-url="rfiUrl" :data-ignore-url="ignoreUrl"></div>',
            props: ["approveUrl", "approveOnHoldUrl", "rejectUrl", "rfiUrl", "ignoreUrl", "requestId", "requestStatus", "membershipTypeName", "requestTarget", "canRequestInfo", "showOnHoldApprove", "csrfToken"],
          },
        },
      },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Membership Request #42");
    expect(wrapper.find('a[href="/membership/requests/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/user/alice/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/organization/7/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/email-tools/send-mail/?to=bob"]').exists()).toBe(true);

    const notesCard = wrapper.get('[data-test="notes-card"]');
    expect(notesCard.attributes("data-summary-url")).toBe("/api/v1/membership/notes/42/summary");
    expect(notesCard.attributes("data-detail-url")).toBe("/api/v1/membership/notes/42");
    expect(notesCard.attributes("data-add-url")).toBe("/api/v1/membership/notes/42/add");
    expect(notesCard.attributes("data-next-url")).toBe("/membership/request/42/");

    const actionsCard = wrapper.get('[data-test="actions-card"]');
    expect(actionsCard.attributes("data-approve-url")).toBe("/api/v1/membership/request/42/approve");
    expect(actionsCard.attributes("data-reject-url")).toBe("/api/v1/membership/request/42/reject");
    expect(actionsCard.attributes("data-rfi-url")).toBe("/api/v1/membership/request/42/rfi");
    expect(actionsCard.attributes("data-ignore-url")).toBe("/api/v1/membership/request/42/ignore");
  });

  it("renders self-service organization copy parity for on-hold requests", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          viewer: {
            mode: "self_service",
          },
          request: {
            id: 42,
            status: "on_hold",
            requested_at: "2026-04-26T10:00:00+00:00",
              requested_by: { show: false, username: "", full_name: "", deleted: false },
              requested_for: { show: true, kind: "organization", label: "Example Org", username: "", organization_id: 7, deleted: false },
            membership_type: { name: "Mirror" },
            responses: [],
          },
          self_service: {
            can_resubmit: true,
            can_rescind: true,
            committee_email: "committee@example.com",
            user_email: "alice@example.com",
            form: {
              fields: [
                { name: "q_domain", label: "Domain name of the mirror", widget: "text", value: "mirror.example.org", required: true, disabled: false, help_text: "", errors: [] },
              ],
              non_field_errors: [],
            },
          },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipRequestDetailPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    const detailLabels = wrapper.findAll("dt").map((node) => node.text());

    expect(wrapper.text()).toContain("On Hold");
    expect(detailLabels).toContain("Organization");
    expect(detailLabels).not.toContain("Requested for");
    expect(wrapper.text()).toContain("Please update your request below and submit it to resume review.");
  });

  it("surfaces compatibility-mode validation errors for self-service resubmission and rereads detail on success", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            viewer: {
              mode: "self_service",
            },
            request: {
              id: 42,
              status: "on_hold",
              requested_at: "2026-04-26T10:00:00+00:00",
              requested_by: { show: false, username: "", full_name: "", deleted: false },
              requested_for: { show: false, kind: "user", label: "", username: "", organization_id: null, deleted: false },
              membership_type: { name: "Mirror" },
              responses: [],
            },
            self_service: {
              can_resubmit: true,
              can_rescind: true,
              committee_email: "committee@example.com",
              user_email: "alice@example.com",
              form: {
                fields: [
                  { name: "q_domain", label: "Domain name of the mirror", widget: "text", value: "mirror.example.org", required: true, disabled: false, help_text: "", errors: [] },
                  { name: "q_pull_request", label: "Please provide a link to your pull request on https://github.com/AlmaLinux/mirrors/", widget: "text", value: "https://github.com/AlmaLinux/mirrors/pull/123", required: true, disabled: false, help_text: "", errors: [] },
                ],
                non_field_errors: [],
              },
            },
          }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: false,
            redirect_url: null,
            reread_targets: [],
            field_errors: { q_domain: ["Enter a valid URL."] },
            non_field_errors: [],
          }),
          { status: 400 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: true,
            redirect_url: null,
            reread_targets: ["detail"],
          }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            viewer: {
              mode: "self_service",
            },
            request: {
              id: 42,
              status: "pending",
              requested_at: "2026-04-26T10:00:00+00:00",
              requested_by: { show: false, username: "", full_name: "", deleted: false },
              requested_for: { show: false, kind: "user", label: "", username: "", organization_id: null, deleted: false },
              membership_type: { name: "Mirror" },
              responses: [],
            },
            self_service: {
              can_resubmit: false,
              can_rescind: true,
              committee_email: "committee@example.com",
              user_email: "alice@example.com",
              form: null,
            },
          }),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipRequestDetailPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    await wrapper.get('input[name="q_domain"]').setValue("not a url");
    await wrapper.get("form").trigger("submit.prevent");
    await flushPromises();

    expect(wrapper.text()).toContain("Enter a valid URL.");

    await wrapper.get('input[name="q_domain"]').setValue("mirror.example.org");
    await wrapper.get("form").trigger("submit.prevent");
    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(wrapper.text()).toContain("Pending");
  });

  it("renders a bootstrap-compatible rescind confirmation form for self-service viewers", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          viewer: {
            mode: "self_service",
          },
          request: {
            id: 42,
            status: "pending",
            requested_at: "2026-04-26T10:00:00+00:00",
            requested_by: { show: false, username: "", full_name: "", deleted: false },
            requested_for: { show: false, kind: "user", label: "", username: "", organization_id: null, deleted: false },
            membership_type: { name: "Mirror" },
            responses: [],
          },
          self_service: {
            can_resubmit: false,
            can_rescind: true,
            committee_email: "committee@example.com",
            user_email: "alice@example.com",
            form: null,
          },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipRequestDetailPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.get('[data-test="rescind-request"]').attributes("data-toggle")).toBe("modal");
    expect(wrapper.get('[data-test="rescind-request"]').attributes("data-target")).toBe("#rescind-confirm-modal");
    expect(wrapper.get('#rescind-confirm-modal form').attributes("action")).toBe("/membership/request/42/rescind/");
    expect(wrapper.get('#rescind-confirm-modal input[name="csrfmiddlewaretoken"]').element.getAttribute("value")).toBe("csrf-token");
  });
});