import { afterEach, describe, expect, it, vi } from "vitest";

import { mountMembershipSponsorsPage } from "../../entrypoints/membershipSponsors";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-membership-sponsors-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountMembershipSponsorsPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts when required sponsors bootstrap data exists", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ draw: 1, recordsTotal: 0, recordsFiltered: 0, data: [] }))),
    );

    const root = buildRoot({
      "data-membership-sponsors-api-url": "/api/v1/membership/sponsors",
      "data-membership-sponsors-page-size": "25",
      "data-membership-sponsors-initial-q": "",
    });

    const app = mountMembershipSponsorsPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-membership-sponsors-vue-root]"))?.not.toBeNull();
  });

  it("does not mount when required bootstrap data is missing", () => {
    const root = buildRoot({
      "data-membership-sponsors-page-size": "25",
    });

    const app = mountMembershipSponsorsPage(root);

    expect(app).toBeNull();
    expect(root.innerHTML).toBe("");
  });
});
