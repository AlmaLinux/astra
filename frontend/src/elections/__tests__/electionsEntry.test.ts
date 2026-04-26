import { afterEach, describe, expect, it, vi } from "vitest";

import { mountElectionsPage } from "../../entrypoints/elections";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-elections-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountElectionsPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts when required elections bootstrap data exists", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            can_manage_elections: false,
            items: [],
            pagination: {
              count: 0,
              page: 1,
              num_pages: 1,
              page_numbers: [1],
              show_first: false,
              show_last: false,
              has_previous: false,
              has_next: false,
              previous_page_number: null,
              next_page_number: null,
              start_index: 0,
              end_index: 0,
            },
          }),
        ),
      ),
    );

    const root = buildRoot({
      "data-elections-api-url": "/api/v1/elections",
      "data-elections-detail-url-template": "/elections/__election_id__/",
      "data-elections-edit-url-template": "/elections/__election_id__/edit/",
    });

    const app = mountElectionsPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-elections-vue-root]")).not.toBeNull();
  });

  it("does not mount when required elections bootstrap data is missing", () => {
    const root = buildRoot({});

    const app = mountElectionsPage(root);

    expect(app).toBeNull();
    expect(root.innerHTML).toBe("");
  });
});