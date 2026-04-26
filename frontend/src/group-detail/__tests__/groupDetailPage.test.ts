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

const bootstrap: GroupDetailBootstrap = {
  infoApiUrl: "/api/v1/groups/infra/info",
  leadersApiUrl: "/api/v1/groups/infra/leaders",
  membersApiUrl: "/api/v1/groups/infra/members",
  actionUrl: "/api/v1/groups/infra/action",
  currentUsername: "admin",
  detailUrlTemplate: "/group/__group_name__/",
  editUrlTemplate: "/group/__group_name__/edit/",
  agreementDetailUrlTemplate: "/settings/?tab=agreements&agreement=__agreement_cn__",
  agreementsListUrl: "/settings/?tab=agreements",
};

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
});
