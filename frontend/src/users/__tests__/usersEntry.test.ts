import { afterEach, describe, expect, it, vi } from "vitest";

import { mountUsersPage } from "../../entrypoints/users";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-users-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountUsersPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts when required users bootstrap data exists", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ users: [], pagination: { count: 0, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 0, end_index: 0 } }))),
    );

    const root = buildRoot({
      "data-users-api-url": "/api/v1/users",
    });

    const app = mountUsersPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-users-vue-root]"))?.not.toBeNull();
  });

  it("does not mount when users bootstrap data is missing", () => {
    const root = buildRoot({});

    const app = mountUsersPage(root);

    expect(app).toBeNull();
    expect(root.innerHTML).toBe("");
  });
});
