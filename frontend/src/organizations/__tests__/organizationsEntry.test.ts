import { afterEach, describe, expect, it, vi } from "vitest";

import { mountOrganizationsPage } from "../../entrypoints/organizations";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-organizations-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountOrganizationsPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts when required organizations bootstrap data exists", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ my_organization: null, my_organization_create_url: "/organizations/create/", sponsor_card: { title: "AlmaLinux Sponsor Members", q: "", items: [], empty_label: "No AlmaLinux sponsor members found.", pagination: { count: 0, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 0, end_index: 0 } }, mirror_card: { title: "Mirror Sponsor Members", q: "", items: [], empty_label: "No mirror sponsor members found.", pagination: { count: 0, page: 1, num_pages: 1, page_numbers: [1], show_first: false, show_last: false, has_previous: false, has_next: false, previous_page_number: null, next_page_number: null, start_index: 0, end_index: 0 } } }))),
    );

    const root = buildRoot({
      "data-organizations-api-url": "/api/v1/organizations",
    });

    const app = mountOrganizationsPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-organizations-vue-root]"))?.not.toBeNull();
  });

  it("does not mount when required organizations bootstrap data is missing", () => {
    const root = buildRoot({});

    const app = mountOrganizationsPage(root);

    expect(app).toBeNull();
    expect(root.innerHTML).toBe("");
  });
});
