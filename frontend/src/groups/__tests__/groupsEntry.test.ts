import { afterEach, describe, expect, it, vi } from "vitest";

import { mountGroupsPage } from "../../entrypoints/groups";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-groups-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountGroupsPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts when required groups bootstrap data exists", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ q: "", items: [], pagination: { count: 0, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 0, end_index: 0 } }))),
    );

    const root = buildRoot({
      "data-groups-api-url": "/api/v1/groups",
    });

    const app = mountGroupsPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-groups-vue-root]")).not.toBeNull();
  });

  it("does not mount when required groups bootstrap data is missing", () => {
    const root = buildRoot({});

    const app = mountGroupsPage(root);

    expect(app).toBeNull();
    expect(root.innerHTML).toBe("");
  });
});
