import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import GroupDetailPage from "../GroupDetailPage.vue";
import type { GroupDetailBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

function deferredResponse(): {
  promise: Promise<Response>;
  resolve: (response: Response) => void;
} {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((innerResolve) => {
    resolve = innerResolve;
  });
  return { promise, resolve };
}

const bootstrap = {
  infoApiUrl: "/api/v1/groups/infra/info",
  leadersApiUrl: "/api/v1/groups/infra/leaders",
  membersApiUrl: "/api/v1/groups/infra/members",
  actionUrl: "/api/v1/groups/infra/action",
  currentUsername: "admin",
  detailUrlTemplate: "/group/__group_name__/",
  editUrlTemplate: "/group/__group_name__/edit/",
  agreementDetailUrlTemplate: "/settings/?tab=agreements&agreement=__agreement_cn__",
  agreementsListUrl: "/settings/?tab=agreements",
  chatConfig: {
    irc: { defaultServer: "irc.libera.chat" },
    matrix: { defaultServer: "matrix.org" },
    mattermost: { defaultServer: "chat.almalinux.org", defaultTeam: "almalinux" },
    matrixToArgs: "web-instance[element.io]=app.element.io",
  },
} as unknown as GroupDetailBootstrap;

describe("GroupDetailPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads and renders group details and members", async () => {
    const fetchMock = vi.fn(async (input) => {
      const url = String(input);
      if (url.includes("/info")) {
        return new Response(
          JSON.stringify({
            group: {
              cn: "infra",
              description: "Infrastructure",
              fas_url: "https://example.org/group/infra",
              fas_mailing_list: "infra@example.org",
              fas_discussion_url: "",
              fas_irc_channels: ["irc://#infra"],
              member_count: 2,
              is_member: true,
              is_sponsor: true,
              sponsor_groups: [{ cn: "ops" }],
              required_agreements: [],
              unsigned_usernames: [],
            },
          }),
        );
      }
      if (url.includes("/leaders")) {
        return new Response(
          JSON.stringify({
            leaders: {
              items: [
                { kind: "group", cn: "ops" },
                { kind: "user", username: "alice", full_name: "Alice Example", avatar_url: "/avatars/alice.png" },
              ],
              pagination: {
                count: 2,
                page: 1,
                num_pages: 1,
                page_numbers: [1],
                show_first: false,
                show_last: false,
                has_previous: false,
                has_next: false,
                previous_page_number: null,
                next_page_number: null,
                start_index: 1,
                end_index: 2,
              },
            },
          }),
        );
      }
      return new Response(
        JSON.stringify({
          members: {
            q: "",
            items: [
              { username: "alice", full_name: "Alice Example", avatar_url: "/avatars/alice.png", is_leader: true },
              { username: "bob", full_name: "Bob Example", avatar_url: "/avatars/bob.png", is_leader: false },
            ],
            pagination: {
              count: 2,
              page: 1,
              num_pages: 1,
              page_numbers: [1],
              show_first: false,
              show_last: false,
              has_previous: false,
              has_next: false,
              previous_page_number: null,
              next_page_number: null,
              start_index: 1,
              end_index: 2,
            },
          },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(GroupDetailPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(wrapper.text()).toContain("infra");
    expect(wrapper.text()).toContain("Infrastructure");
    expect(wrapper.text()).toContain("Alice Example");
    expect(wrapper.text()).toContain("Bob Example");
    expect(wrapper.text()).toContain("alice");
    expect(wrapper.text()).toContain("bob");
    expect(wrapper.find('img[src="/avatars/alice.png"]').exists()).toBe(true);
    expect(wrapper.find('.card-tools a[href="/group/infra/edit/"]').exists()).toBe(true);
    expect(wrapper.find('a[href="/group/infra/edit/"]').exists()).toBe(true);
  });

  it("renders linked chat channels when values are parseable and plain text otherwise", async () => {
    const fetchMock = vi.fn(async (input) => {
      const url = String(input);
      if (url.includes("/info")) {
        return new Response(
          JSON.stringify({
            group: {
              cn: "infra",
              description: "Infrastructure",
              fas_url: "",
              fas_mailing_list: "",
              fas_discussion_url: "",
              fas_irc_channels: ["irc://#infra", "mattermost://channels/atomicsig", "not a channel"],
              member_count: 2,
              is_member: true,
              is_sponsor: true,
              sponsor_groups: [],
              required_agreements: [],
              unsigned_usernames: [],
            },
          }),
        );
      }
      if (url.includes("/leaders")) {
        return new Response(JSON.stringify({ leaders: { items: [], pagination: { count: 0, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 0, end_index: 0 } } }));
      }
      return new Response(JSON.stringify({ members: { q: "", items: [], pagination: { count: 0, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 0, end_index: 0 } } }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(GroupDetailPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.find('a[href="ircs://irc.libera.chat/#infra"]').text()).toBe("#infra");
    expect(wrapper.find('a[href="https://chat.almalinux.org/almalinux/channels/atomicsig"]').text()).toBe("~atomicsig");
    expect(wrapper.findAll(".profile-chat-item").some((item) => item.text().includes("not a channel"))).toBe(true);
    expect(wrapper.find('a[href="not a channel"]').exists()).toBe(false);
  });

  it("renders the info card and loading states before leaders and members finish loading", async () => {
    const leadersDeferred = deferredResponse();
    const membersDeferred = deferredResponse();
    const fetchMock = vi.fn((input) => {
      const url = String(input);
      if (url.includes("/info")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              group: {
                cn: "infra",
                description: "Infrastructure",
                fas_url: "",
                fas_mailing_list: "",
                fas_discussion_url: "",
                fas_irc_channels: [],
                member_count: 0,
                is_member: true,
                is_sponsor: false,
                required_agreements: [],
                unsigned_usernames: [],
              },
            }),
          ),
        );
      }
      if (url.includes("/leaders")) {
        return leadersDeferred.promise;
      }
      return membersDeferred.promise;
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(GroupDetailPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(wrapper.text()).toContain("Group: infra");
    expect(wrapper.text()).toContain("Infrastructure");
    expect(wrapper.text()).toContain("Group info");
    expect(wrapper.findAll(".spinner-border").length).toBeGreaterThanOrEqual(2);
  });

  it("renders shared user widget grids for team leads and members", async () => {
    const fetchMock = vi.fn(async (input) => {
      const url = String(input);
      if (url.includes("/info")) {
        return new Response(JSON.stringify({ group: { cn: "infra", description: "Infrastructure", fas_url: "", fas_mailing_list: "", fas_discussion_url: "", fas_irc_channels: [], member_count: 2, is_member: true, is_sponsor: true, sponsor_groups: [], required_agreements: [], unsigned_usernames: ["bob"] } }));
      }
      if (url.includes("/leaders")) {
        return new Response(JSON.stringify({ leaders: { items: [{ kind: "user", username: "alice", full_name: "Alice Example", avatar_url: "/avatars/alice.png" }], pagination: { count: 1, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 1 } } }));
      }
      return new Response(JSON.stringify({ members: { q: "", items: [{ username: "alice", full_name: "Alice Example", avatar_url: "/avatars/alice.png", is_leader: true }, { username: "bob", full_name: "Bob Example", avatar_url: "/avatars/bob.png", is_leader: false }], pagination: { count: 2, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 2 } } }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(GroupDetailPage, {
      props: {
        bootstrap: {
          ...bootstrap,
          currentUsername: "admin",
        },
      },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.findAll(".widget-user").length).toBeGreaterThanOrEqual(3);
    expect(wrapper.find("table").exists()).toBe(false);
  });

  it("right-aligns member search and hides promote for existing team leads", async () => {
    const fetchMock = vi.fn(async (input) => {
      const url = String(input);
      if (url.includes("/info")) {
        return new Response(JSON.stringify({ group: { cn: "infra", description: "Infrastructure", fas_url: "", fas_mailing_list: "", fas_discussion_url: "", fas_irc_channels: [], member_count: 2, is_member: true, is_sponsor: true, sponsor_groups: [], required_agreements: [], unsigned_usernames: [] } }));
      }
      if (url.includes("/leaders")) {
        return new Response(JSON.stringify({ leaders: { items: [{ kind: "user", username: "alice", full_name: "Alice Example", avatar_url: "/avatars/alice.png" }], pagination: { count: 1, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 1 } } }));
      }
      return new Response(JSON.stringify({ members: { q: "", items: [{ username: "alice", full_name: "Alice Example", avatar_url: "/avatars/alice.png", is_leader: true }, { username: "bob", full_name: "Bob Example", avatar_url: "/avatars/bob.png", is_leader: false }], pagination: { count: 2, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 2 } } }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(GroupDetailPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.find(".card-tools .input-group").exists()).toBe(true);
    expect(wrapper.findAll('button[aria-label="Promote to Team Lead"]').length).toBe(1);
    expect(wrapper.findAll('button[aria-label="Remove member"]').length).toBe(2);
  });

  it("paginates members without refetching info or leaders", async () => {
    const fetchMock = vi.fn(async (input) => {
      const url = String(input);
      if (url.includes("/info")) {
        return new Response(JSON.stringify({ group: { cn: "infra", description: "Infrastructure", fas_url: "", fas_mailing_list: "", fas_discussion_url: "", fas_irc_channels: [], member_count: 4, is_member: true, is_sponsor: true, required_agreements: [], unsigned_usernames: [] } }));
      }
      if (url.includes("/leaders")) {
        return new Response(JSON.stringify({ leaders: { items: [{ kind: "user", username: "alice", full_name: "Alice Example", avatar_url: "/avatars/alice.png" }], pagination: { count: 1, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 1 } } }));
      }
      if (url.includes("page=2")) {
        return new Response(JSON.stringify({ members: { q: "", items: [{ username: "carol", full_name: "Carol Example", avatar_url: "/avatars/carol.png", is_leader: false }, { username: "dave", full_name: "Dave Example", avatar_url: "/avatars/dave.png", is_leader: false }], pagination: { count: 4, page: 2, num_pages: 2, page_numbers: [2], show_first: true, show_last: false, has_previous: true, has_next: false, previous_page_number: 1, next_page_number: null, start_index: 3, end_index: 4 } } }));
      }
      return new Response(JSON.stringify({ members: { q: "", items: [{ username: "alice", full_name: "Alice Example", avatar_url: "/avatars/alice.png", is_leader: true }, { username: "bob", full_name: "Bob Example", avatar_url: "/avatars/bob.png", is_leader: false }], pagination: { count: 4, page: 1, num_pages: 2, page_numbers: [1, 2], show_first: false, show_last: false, has_previous: false, has_next: true, previous_page_number: null, next_page_number: 2, start_index: 1, end_index: 2 } } }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(GroupDetailPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(3);

    await wrapper.findAll('a[aria-label="Next"]')[0].trigger("click");
    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(String(fetchMock.mock.calls[3]?.[0] || "")).toContain("/members?page=2");
    expect(wrapper.text()).toContain("Carol Example");
    expect(wrapper.text()).not.toContain("Bob Example");
  });

  it("paginates leaders without refetching info or members", async () => {
    const fetchMock = vi.fn(async (input) => {
      const url = String(input);
      if (url.includes("/info")) {
        return new Response(JSON.stringify({ group: { cn: "infra", description: "Infrastructure", fas_url: "", fas_mailing_list: "", fas_discussion_url: "", fas_irc_channels: [], member_count: 2, is_member: true, is_sponsor: true, required_agreements: [], unsigned_usernames: [] } }));
      }
      if (url.includes("leaders?page=2")) {
        return new Response(JSON.stringify({ leaders: { items: [{ kind: "user", username: "carol", full_name: "Carol Lead", avatar_url: "/avatars/carol.png" }], pagination: { count: 3, page: 2, num_pages: 2, page_numbers: [2], show_first: true, show_last: false, has_previous: true, has_next: false, previous_page_number: 1, next_page_number: null, start_index: 3, end_index: 3 } } }));
      }
      if (url.includes("/leaders")) {
        return new Response(JSON.stringify({ leaders: { items: [{ kind: "group", cn: "ops" }, { kind: "user", username: "alice", full_name: "Alice Example", avatar_url: "/avatars/alice.png" }], pagination: { count: 3, page: 1, num_pages: 2, page_numbers: [1, 2], show_first: false, show_last: false, has_previous: false, has_next: true, previous_page_number: null, next_page_number: 2, start_index: 1, end_index: 2 } } }));
      }
      return new Response(JSON.stringify({ members: { q: "", items: [{ username: "alice", full_name: "Alice Example", avatar_url: "/avatars/alice.png", is_leader: true }, { username: "bob", full_name: "Bob Example", avatar_url: "/avatars/bob.png", is_leader: false }], pagination: { count: 2, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 2 } } }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(GroupDetailPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(3);

    await wrapper.findAll('a[aria-label="Next"]')[0].trigger("click");
    await flushPromises();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(String(fetchMock.mock.calls[3]?.[0] || "")).toContain("/leaders?page=2");
    expect(wrapper.text()).toContain("Carol Lead");
    expect(wrapper.text()).toContain("Alice Example");
    expect(wrapper.text()).not.toContain("ops");
  });

  it("renders member groups inline in the Members grid and posts add member group actions", async () => {
    const fetchMock = vi.fn(async (input, init) => {
      const url = String(input);
      if (init?.method === "POST") {
        return new Response(JSON.stringify({ ok: true }));
      }
      if (url.includes("/info")) {
        return new Response(JSON.stringify({ group: { cn: "infra", description: "Infrastructure", fas_url: "", fas_mailing_list: "", fas_discussion_url: "", fas_irc_channels: [], member_count: 2, is_member: true, is_sponsor: true, required_agreements: [], unsigned_usernames: [] } }));
      }
      if (url.includes("/leaders")) {
        return new Response(JSON.stringify({ leaders: { items: [], pagination: { count: 0, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 0, end_index: 0 } } }));
      }
      return new Response(JSON.stringify({ members: { q: "", items: [{ username: "alice", full_name: "Alice Example", avatar_url: "/avatars/alice.png", is_leader: true }], pagination: { count: 1, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 1 } }, member_groups: { items: [{ cn: "child-a" }, { cn: "child-b" }] } }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(GroupDetailPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    const cardTitles = wrapper.findAll(".card-title").map((title) => title.text());
    const membersCard = wrapper.findAll(".card").find((card) => card.find(".card-title").text() === "Members");

    expect(cardTitles).not.toContain("Member groups");
    expect(membersCard).toBeTruthy();
    expect(membersCard?.find(".border-top").exists()).toBe(false);
    expect(membersCard?.findAll(".row")).toHaveLength(1);
    expect(membersCard?.text()).toContain("child-a");
    expect(membersCard?.text()).toContain("child-b");
    const membersCardText = membersCard?.text() || "";
    expect(membersCardText.indexOf("child-a")).toBeGreaterThan(-1);
    expect(membersCardText.indexOf("Alice Example")).toBeGreaterThan(-1);
    expect(membersCardText.indexOf("child-a")).toBeLessThan(membersCardText.indexOf("Alice Example"));

    await wrapper.get('input[placeholder="Add member group by name"]').setValue("child-c");
    await wrapper.get('form[data-member-group-form="true"]').trigger("submit");
    await flushPromises();
    await flushPromises();

    const postCall = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(postCall).toBeTruthy();
    expect(postCall?.[0]).toBe("/api/v1/groups/infra/action");
    expect(postCall?.[1]?.body).toBe(JSON.stringify({ action: "add_member_group", group_name: "child-c" }));
  });

  it("posts remove member group actions through the confirm flow", async () => {
    const fetchMock = vi.fn(async (input, init) => {
      const url = String(input);
      if (init?.method === "POST") {
        return new Response(JSON.stringify({ ok: true }));
      }
      if (url.includes("/info")) {
        return new Response(JSON.stringify({ group: { cn: "infra", description: "Infrastructure", fas_url: "", fas_mailing_list: "", fas_discussion_url: "", fas_irc_channels: [], member_count: 2, is_member: true, is_sponsor: true, required_agreements: [], unsigned_usernames: [] } }));
      }
      if (url.includes("/leaders")) {
        return new Response(JSON.stringify({ leaders: { items: [], pagination: { count: 0, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 0, end_index: 0 } } }));
      }
      return new Response(JSON.stringify({ members: { q: "", items: [{ username: "alice", full_name: "Alice Example", avatar_url: "/avatars/alice.png", is_leader: true }], pagination: { count: 1, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 1 } }, member_groups: { items: [{ cn: "child-a" }] } }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(GroupDetailPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    await wrapper.get('button[aria-label="Remove member group"]').trigger("click");
    expect(wrapper.text()).toContain("Remove member group?");
    expect(wrapper.text()).toContain("Remove child-a from this group?");

    const postCallsBeforeConfirm = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(postCallsBeforeConfirm).toHaveLength(0);

    const confirmButtons = wrapper.findAll("button").filter((button) => button.text() === "Confirm");
    expect(confirmButtons).toHaveLength(1);
    await confirmButtons[0].trigger("click");
    await flushPromises();
    await flushPromises();

    const postCalls = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(postCalls).toHaveLength(1);
    expect(postCalls[0]?.[0]).toBe("/api/v1/groups/infra/action");
    expect(postCalls[0]?.[1]?.body).toBe(JSON.stringify({ action: "remove_member_group", group_name: "child-a" }));
  });

  it("renders parity actions for nested groups and posts Team Lead group mutations", async () => {
    const fetchMock = vi.fn(async (input, init) => {
      const url = String(input);
      if (init?.method === "POST") {
        return new Response(JSON.stringify({ ok: true }));
      }
      if (url.includes("/info")) {
        return new Response(JSON.stringify({ group: { cn: "infra", description: "Infrastructure", fas_url: "", fas_mailing_list: "", fas_discussion_url: "", fas_irc_channels: [], member_count: 2, is_member: true, is_sponsor: true, required_agreements: [], unsigned_usernames: [] } }));
      }
      if (url.includes("/leaders")) {
        return new Response(JSON.stringify({ leaders: { items: [{ kind: "group", cn: "child-leads" }], pagination: { count: 1, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 1 } } }));
      }
      return new Response(JSON.stringify({ members: { q: "", items: [], pagination: { count: 2, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 2 } }, member_groups: { items: [{ cn: "child-member" }, { cn: "child-leads" }] } }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(GroupDetailPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.findAll('button[aria-label="Promote group to Team Lead"]').length).toBe(1);
    expect(wrapper.findAll('button[aria-label="Remove Team Lead"]').length).toBe(1);
    expect(wrapper.findAll('button[aria-label="Remove member group"]').length).toBe(2);

    const promoteButton = wrapper.get('button[aria-label="Promote group to Team Lead"]');
    expect(promoteButton.find("i").classes()).toContain("fa-person-arrow-up-from-line");

    const removeMemberButtons = wrapper.findAll('button[aria-label="Remove member group"]');
    expect(removeMemberButtons[0]?.find("i").classes()).toContain("fa-user-minus");

    expect(wrapper.get('button[aria-label="Remove Team Lead"]').find("i").classes()).toContain("fa-person-arrow-down-to-line");

    await promoteButton.trigger("click");
    expect(wrapper.text()).toContain("Promote member group to Team Lead?");
    expect(wrapper.text()).toContain("Promote child-member to Team Lead for this group?");

    let confirmButtons = wrapper.findAll("button").filter((button) => button.text() === "Confirm");
    expect(confirmButtons).toHaveLength(1);
    await confirmButtons[0].trigger("click");
    await flushPromises();
    await flushPromises();

    await wrapper.get('button[aria-label="Remove Team Lead"]').trigger("click");
    expect(wrapper.text()).toContain("Demote Team Lead?");
    expect(wrapper.text()).toContain("Remove Team Lead access for child-leads?");

    confirmButtons = wrapper.findAll("button").filter((button) => button.text() === "Confirm");
    expect(confirmButtons).toHaveLength(1);
    await confirmButtons[0].trigger("click");
    await flushPromises();
    await flushPromises();

    const postCalls = fetchMock.mock.calls.filter(([, callInit]) => callInit?.method === "POST");
    expect(postCalls).toHaveLength(2);
    expect(postCalls[0]?.[1]?.body).toBe(JSON.stringify({ action: "promote_member_group", group_name: "child-member" }));
    expect(postCalls[1]?.[1]?.body).toBe(JSON.stringify({ action: "demote_sponsor_group", group_name: "child-leads" }));
  });

  it("stacks nested group action buttons so they remain visibly accessible", async () => {
    const fetchMock = vi.fn(async (input) => {
      const url = String(input);
      if (url.includes("/info")) {
        return new Response(JSON.stringify({ group: { cn: "infra", description: "Infrastructure", fas_url: "", fas_mailing_list: "", fas_discussion_url: "", fas_irc_channels: [], member_count: 2, is_member: true, is_sponsor: true, required_agreements: [], unsigned_usernames: [] } }));
      }
      if (url.includes("/leaders")) {
        return new Response(JSON.stringify({ leaders: { items: [{ kind: "group", cn: "child-leads" }], pagination: { count: 1, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 1 } } }));
      }
      return new Response(JSON.stringify({ members: { q: "", items: [], pagination: { count: 2, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 2 } }, member_groups: { items: [{ cn: "child-member" }, { cn: "child-leads" }] } }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(GroupDetailPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    const memberGroupButtons = wrapper.findAll('button[aria-label="Remove member group"], button[aria-label="Promote group to Team Lead"]');
    expect(memberGroupButtons).toHaveLength(3);

    const childMemberCard = memberGroupButtons
      .map((button) => button.element.closest(".position-relative"))
      .find((card) => card?.textContent?.includes("child-member"));
    expect(childMemberCard).toBeTruthy();

    const childMemberActions = Array.from(childMemberCard?.querySelectorAll("button") || []);
    expect(childMemberActions).toHaveLength(2);
    expect(childMemberActions[0]?.getAttribute("style")).not.toBe(childMemberActions[1]?.getAttribute("style"));
    expect(childMemberActions.every((button) => button.classList.contains("position-absolute"))).toBe(true);
  });
});
