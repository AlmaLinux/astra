import { afterEach, describe, expect, it, vi } from "vitest";

import { mountGroupDetailPage } from "../../entrypoints/groupDetail";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-group-detail-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountGroupDetailPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts when required group detail bootstrap data exists", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input) => {
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
                fas_irc_channels: [],
                member_count: 1,
                is_member: true,
                is_sponsor: false,
                sponsor_groups: [],
                required_agreements: [],
                unsigned_usernames: [],
                edit_url: "/group/infra/edit/",
              },
            }),
          );
        }
        if (url.includes("/leaders")) {
          return new Response(
            JSON.stringify({
              leaders: {
                items: [],
                pagination: { count: 0, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 0, end_index: 0 },
              },
            }),
          );
        }
        return new Response(
          JSON.stringify({
            members: { q: "", items: [{ username: "alice", full_name: "Alice Example", avatar_url: "" }], pagination: { count: 1, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 1, end_index: 1 } },
          }),
        );
      }),
    );

    const root = buildRoot({
      "data-group-detail-info-api-url": "/api/v1/groups/infra/info",
      "data-group-detail-leaders-api-url": "/api/v1/groups/infra/leaders",
      "data-group-detail-members-api-url": "/api/v1/groups/infra/members",
      "data-group-detail-action-url": "/api/v1/groups/infra/action",
      "data-group-detail-url-template": "/group/__group_name__/",
      "data-group-detail-edit-url-template": "/group/__group_name__/edit/",
      "data-group-detail-agreement-detail-url-template": "/settings/?tab=agreements&agreement=__agreement_cn__",
      "data-group-detail-agreements-list-url": "/settings/?tab=agreements",
    });

    const app = mountGroupDetailPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-group-detail-vue-root]")).not.toBeNull();
  });
});
