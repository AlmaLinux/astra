import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import MembershipNotesCard from "../components/MembershipNotesCard.vue";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

describe("MembershipNotesCard", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("starts collapsed without localStorage restore and shows legacy loading placeholders", async () => {
    window.localStorage.setItem("membership_notes_open_15", "true");

    let resolveSummary: ((value: Response) => void) | undefined;
    const fetchMock = vi.fn(
      () => new Promise<Response>((resolve) => {
        resolveSummary = resolve;
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipNotesCard, {
      props: {
        requestId: 15,
        summaryUrl: "/api/v1/membership/notes/15/summary",
        detailUrl: "/api/v1/membership/notes/15",
        addUrl: "/membership/requests/15/notes/add/",
        requestDetailTemplate: "/membership/request/__request_id__/",
        csrfToken: "csrf-token",
        nextUrl: "/membership/requests/",
        canView: true,
        canWrite: true,
        canVote: true,
      },
    });

    expect(wrapper.find('[data-membership-notes-card="15"]').classes()).toContain("collapsed-card");
    expect(wrapper.find('[data-membership-notes-count="15"]').text()).toBe("...");
    expect(wrapper.find('[data-membership-notes-count="15"]').attributes("title")).toBe("Loading note summary");
    expect(wrapper.find('[data-membership-notes-approvals="15"]').text()).toContain("...");
    expect(wrapper.find('[data-membership-notes-disapprovals="15"]').text()).toContain("...");
    expect(wrapper.find(".card-footer").exists()).toBe(true);

    resolveSummary?.(new Response(JSON.stringify({ note_count: 3, approvals: 1, disapprovals: 0, current_user_vote: null })));
    await flushPromises();
    await flushPromises();

    expect(wrapper.find('[data-membership-notes-count="15"]').text()).toBe("3");
    expect(wrapper.find('[data-membership-notes-count="15"]').attributes("title")).toBe("3 Messages");
  });

  it("loads summary eagerly, fetches detail lazily, and rereads after a successful post", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") {
        return new Response(JSON.stringify({ ok: true, message: "Note added." }));
      }
      if (url.endsWith("/summary")) {
        return new Response(
          JSON.stringify({
            note_count: 2,
            approvals: 1,
            disapprovals: 0,
            current_user_vote: "approve",
          }),
        );
      }
      return new Response(
        JSON.stringify({
          groups: [
            {
              username: "reviewer",
              display_username: "reviewer",
              is_self: true,
              is_custos: false,
              avatar_kind: "user",
              avatar_url: "",
              timestamp_display: "April 21, 2026, noon",
              entries: [
                {
                  kind: "message",
                  rendered_html: "<p>First note</p>",
                  is_self: true,
                  is_custos: false,
                  bubble_style: "",
                },
              ],
            },
          ],
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipNotesCard, {
      props: {
        requestId: 10,
        summaryUrl: "/api/v1/membership/notes/10/summary",
        detailUrl: "/api/v1/membership/notes/10",
        addUrl: "/membership/requests/10/notes/add/",
        requestDetailTemplate: "/membership/request/__request_id__/",
        csrfToken: "csrf-token",
        nextUrl: "/membership/requests/",
        canView: true,
        canWrite: true,
        canVote: true,
      },
    });

    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(wrapper.find('[data-membership-notes-card="10"]').classes()).toEqual(
      expect.arrayContaining(["card", "card-primary", "card-outline", "direct-chat", "direct-chat-primary", "collapsed-card"]),
    );
    expect(wrapper.find(".membership-notes-header-compact").exists()).toBe(true);
    expect(wrapper.find(".membership-notes-title").text()).toContain("Membership Committee Notes");
    expect(wrapper.find('[data-membership-notes-count="10"]').classes()).toContain("badge-primary");
    expect(wrapper.find('[data-membership-notes-approvals="10"]').classes()).toContain("badge-warning");
    expect(wrapper.find('[data-membership-notes-disapprovals="10"]').classes()).toContain("badge-danger");
    expect(wrapper.find('[data-membership-notes-collapse="10"]').classes()).toEqual(expect.arrayContaining(["btn", "btn-tool"]));
    expect(wrapper.find('[data-membership-notes-collapse="10"] [aria-hidden="true"]').classes()).toContain("fa-plus");
    expect(wrapper.find(".card-tools").text()).not.toContain("You voted");

    await wrapper.get('[data-membership-notes-toggle="10"]').trigger("click");
    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(wrapper.html()).toContain("First note");
    expect(wrapper.find(".direct-chat-messages").exists()).toBe(true);
    expect(wrapper.find(".direct-chat-msg").exists()).toBe(true);
    expect(wrapper.find(".direct-chat-infos").exists()).toBe(true);
    expect(wrapper.find(".direct-chat-text").exists()).toBe(true);
    expect(wrapper.find('[data-membership-notes-collapse="10"] [aria-hidden="true"]').classes()).toContain("fa-minus");
    expect(wrapper.find(".card-footer").exists()).toBe(true);
    expect(wrapper.find(".card-footer .input-group").exists()).toBe(true);
    expect(wrapper.find('.input-group-append > button[data-note-action="message"]').exists()).toBe(true);
    expect(wrapper.find('.input-group-append .btn-group').exists()).toBe(false);
    expect(wrapper.find('[data-note-action="message"]').attributes("aria-label")).toBe("Send note");
    expect(wrapper.find('[data-note-action="message"] [aria-hidden="true"]').classes()).toContain("fa-paper-plane");
    expect(wrapper.find('[role="group"][aria-label="Vote actions"]').exists()).toBe(true);
    expect(wrapper.find('[data-note-action="vote_approve"]').classes()).toEqual(expect.arrayContaining(["btn-light", "btn-sm", "flex-fill"]));

    await wrapper.get('[data-membership-notes-toggle="10"]').trigger("click");
    await wrapper.get('[data-membership-notes-toggle="10"]').trigger("click");
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(2);

    await wrapper.get('textarea[name="message"]').setValue("Second note");
    await wrapper.get('button[data-note-action="message"]').trigger("click");
    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(5);
    expect(fetchMock.mock.calls[2]?.[1]?.method).toBe("POST");
  });

  it("renders write failures without dropping the existing detail state", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") {
        return new Response(JSON.stringify({ ok: false, error: "Failed to add note." }), { status: 500 });
      }
      if (url.endsWith("/summary")) {
        return new Response(JSON.stringify({ note_count: 0, approvals: 0, disapprovals: 0, current_user_vote: null }));
      }
      return new Response(JSON.stringify({ groups: [] }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipNotesCard, {
      props: {
        requestId: 11,
        summaryUrl: "/api/v1/membership/notes/11/summary",
        detailUrl: "/api/v1/membership/notes/11",
        addUrl: "/membership/requests/11/notes/add/",
        requestDetailTemplate: "/membership/request/__request_id__/",
        csrfToken: "csrf-token",
        nextUrl: "/membership/requests/",
        canView: true,
        canWrite: true,
        canVote: false,
      },
    });

    await flushPromises();
    await wrapper.get('[data-membership-notes-toggle="11"]').trigger("click");
    await flushPromises();
    await wrapper.get('textarea[name="message"]').setValue("Bad note");
    await wrapper.get('button[data-note-action="message"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-membership-notes-approvals="11"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("Failed to add note.");
    expect(wrapper.text()).toContain("No notes yet.");
    expect(wrapper.find(".card-footer .alert .close").exists()).toBe(true);
  });

  it("keeps the form hidden for read-only viewers", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ note_count: 1, approvals: 0, disapprovals: 0, current_user_vote: null }))),
    );

    const wrapper = mount(MembershipNotesCard, {
      props: {
        requestId: 12,
        summaryUrl: "/api/v1/membership/notes/12/summary",
        detailUrl: "/api/v1/membership/notes/12",
        addUrl: "/membership/requests/12/notes/add/",
        requestDetailTemplate: "/membership/request/__request_id__/",
        csrfToken: "csrf-token",
        nextUrl: "/membership/requests/",
        canView: true,
        canWrite: false,
        canVote: false,
      },
    });

    await flushPromises();

    expect(wrapper.find('textarea[name="message"]').exists()).toBe(false);
    expect(wrapper.find('button[data-note-action="message"]').exists()).toBe(false);
  });

  it("submits message on Ctrl+Enter in the composer", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") {
        return new Response(JSON.stringify({ ok: true, message: "Note added." }));
      }
      if (url.endsWith("/summary")) {
        return new Response(JSON.stringify({ note_count: 0, approvals: 0, disapprovals: 0, current_user_vote: null }));
      }
      return new Response(JSON.stringify({ groups: [] }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipNotesCard, {
      props: {
        requestId: 99,
        summaryUrl: "/api/v1/membership/notes/99/summary",
        detailUrl: "/api/v1/membership/notes/99",
        addUrl: "/membership/requests/99/notes/add/",
        requestDetailTemplate: "/membership/request/__request_id__/",
        csrfToken: "csrf-token",
        nextUrl: "/membership/requests/",
        canView: true,
        canWrite: true,
        canVote: false,
      },
    });

    await flushPromises();
    await wrapper.get('[data-membership-notes-toggle="99"]').trigger("click");
    await flushPromises();

    const textarea = wrapper.get('textarea[name="message"]');
    await textarea.setValue("Send via keyboard");
    await textarea.trigger("keydown", { key: "Enter", ctrlKey: true });
    await flushPromises();
    await flushPromises();

    const postCall = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(postCall).toBeDefined();
  });

  it("degrades summary failures to the legacy warning badge state", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/summary")) {
        return new Response(JSON.stringify({ error: "summary failed" }), { status: 503 });
      }
      return new Response(JSON.stringify({ error: "detail failed" }), { status: 503 });
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MembershipNotesCard, {
      props: {
        requestId: 13,
        summaryUrl: "/api/v1/membership/notes/13/summary",
        detailUrl: "/api/v1/membership/notes/13",
        addUrl: "/membership/requests/13/notes/add/",
        requestDetailTemplate: "/membership/request/__request_id__/",
        csrfToken: "csrf-token",
        nextUrl: "/membership/requests/",
        canView: true,
        canWrite: true,
        canVote: false,
      },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.find('[data-membership-notes-count="13"]').text()).toBe("!");
    expect(wrapper.find('[data-membership-notes-count="13"]').classes()).toContain("badge-warning");
    expect(wrapper.find('[data-membership-notes-count="13"]').attributes("title")).toBe("Note summary unavailable");
    expect(wrapper.find(".alert").exists()).toBe(false);

    await wrapper.get('[data-membership-notes-toggle="13"]').trigger("click");
    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain("Failed to load notes.");
    expect(wrapper.find(".card-footer .alert .close").exists()).toBe(true);
  });

  it("renders legacy compact header layout, request links, bubble styling, and diff line breaks", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/summary")) {
          return new Response(JSON.stringify({ note_count: 1, approvals: 0, disapprovals: 1, current_user_vote: "disapprove" }));
        }
        return new Response(
          JSON.stringify({
            groups: [
              {
                username: "custos",
                display_username: "Astra Custodia",
                is_self: false,
                is_custos: true,
                avatar_kind: "custos",
                avatar_url: "/static/core/images/almalinux-logo.svg",
                timestamp_display: "April 21, 2026, noon",
                membership_request_id: 123,
                entries: [
                  {
                    kind: "message",
                    rendered_html: "<p>Styled note</p>",
                    is_self: false,
                    is_custos: true,
                    bubble_style: "background-color: rgb(255, 245, 200); color: rgb(10, 10, 10);",
                  },
                  {
                    kind: "action",
                    label: "Request resubmitted",
                    icon: "fa-sync",
                    bubble_style: "background-color: rgb(240, 240, 240); color: rgb(20, 20, 20);",
                    request_resubmitted_diff_rows: [
                      {
                        question: "Why?",
                        old_value: "Line one\nLine two",
                        new_value: "Updated one\nUpdated two",
                      },
                    ],
                  },
                ],
              },
            ],
          }),
        );
      }),
    );

    const wrapper = mount(MembershipNotesCard, {
      props: {
        requestId: 16,
        summaryUrl: "/api/v1/membership/notes/16/summary",
        detailUrl: "/api/v1/membership/notes/16",
        addUrl: "/membership/requests/16/notes/add/",
        requestDetailTemplate: "/membership/request/__request_id__/",
        csrfToken: "csrf-token",
        nextUrl: "/membership/requests/",
        canView: true,
        canWrite: true,
        canVote: false,
      },
    });

    await flushPromises();
    await wrapper.get('[data-membership-notes-toggle="16"]').trigger("click");
    await flushPromises();
    await flushPromises();

    expect(wrapper.find(".direct-chat-messages").attributes("style")).toContain("max-height: 260px");
    expect(wrapper.find(".direct-chat-infos .direct-chat-name.float-left").text()).toBe("Astra Custodia");
    expect(wrapper.find(".direct-chat-infos .direct-chat-timestamp.float-right").text()).toContain("April 21, 2026, noon");
    expect(wrapper.find('.direct-chat-infos a[href="/membership/request/123/"]').text()).toContain("(req. #123)");
    expect(wrapper.find(".direct-chat-img").attributes("src")).toBe("/static/core/images/almalinux-logo.svg");
    expect(wrapper.findAll(".membership-notes-bubbles .direct-chat-text").at(0)?.attributes("style")).toContain("border: 1px dashed rgba(0, 0, 0, 0.15)");
    expect(wrapper.findAll(".membership-notes-bubbles .direct-chat-text").at(1)?.attributes("style")).toContain("background-color: rgb(240, 240, 240)");
    expect(wrapper.find('[data-request-resubmitted-old]').html()).toContain("Line one<br>");
    expect(wrapper.find('[data-request-resubmitted-new]').html()).toContain("Updated one<br>");
    expect(wrapper.find('[data-membership-notes-disapprovals="16"]').exists()).toBe(false);
  });

  it("restores the legacy contacted-email diagnostics without rendering raw html in the modal body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/summary")) {
          return new Response(JSON.stringify({ note_count: 1, approvals: 0, disapprovals: 0, current_user_vote: null }));
        }
        return new Response(
          JSON.stringify({
            groups: [
              {
                username: "reviewer",
                display_username: "reviewer",
                is_self: true,
                is_custos: false,
                avatar_kind: "user",
                avatar_url: "",
                timestamp_display: "April 21, 2026, noon",
                entries: [
                  {
                    kind: "action",
                    label: "Contacted requester",
                    icon: "fa-envelope",
                    contacted_email: {
                      email_id: 77,
                      to: ["alice@example.com"],
                      cc: ["cc@example.com"],
                      bcc: ["bcc@example.com"],
                      subject: "Approval notice",
                      from_email: "noreply@example.com",
                      reply_to: "committee@example.com",
                      recipient_delivery_summary: "1 delivered",
                      recipient_delivery_summary_note: "Delivered via SES",
                      headers: [["X-Test", "value"]],
                      html: "<img src=x onerror=alert('boom')><p>HTML body</p>",
                      text: "Plain text body",
                      logs: [
                        {
                          date_display: "2026-04-21 12:00:00 UTC",
                          status: "sent",
                          message: "sent",
                          exception_type: "",
                        },
                      ],
                    },
                  },
                ],
              },
            ],
          }),
        );
      }),
    );

    const wrapper = mount(MembershipNotesCard, {
      props: {
        requestId: 14,
        summaryUrl: "/api/v1/membership/notes/14/summary",
        detailUrl: "/api/v1/membership/notes/14",
        addUrl: "/membership/requests/14/notes/add/",
        requestDetailTemplate: "/membership/request/__request_id__/",
        csrfToken: "csrf-token",
        nextUrl: "/membership/requests/",
        canView: true,
        canWrite: false,
        canVote: false,
      },
    });

    await flushPromises();
    await wrapper.get('[data-membership-notes-toggle="14"]').trigger("click");
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("View email");
    expect(wrapper.text()).toContain("From");
    expect(wrapper.text()).toContain("Reply-To");
    expect(wrapper.text()).toContain("Recipient delivery summary");
    expect(wrapper.text()).toContain("Other headers");
    expect(wrapper.text()).toContain("Delivery logs");
    expect(wrapper.text()).toContain("Plain text body");
    expect(wrapper.find('iframe[title="Email HTML preview"]').attributes("srcdoc")).toContain("<img src=x onerror=alert('boom')><p>HTML body</p>");
    expect(wrapper.find('.modal-body img').exists()).toBe(false);
    expect(wrapper.find('.modal-body script').exists()).toBe(false);
  });
});