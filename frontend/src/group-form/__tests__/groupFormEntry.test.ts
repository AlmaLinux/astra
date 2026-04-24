import { afterEach, describe, expect, it, vi } from "vitest";

import { mountGroupFormPage } from "../../entrypoints/groupForm";

function buildRoot(attributes: Record<string, string>): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-group-form-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  document.body.appendChild(root);
  return root;
}

describe("mountGroupFormPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts when required group form bootstrap data exists", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            group: {
              cn: "infra",
              description: "Infra Team",
              fas_url: "",
              fas_mailing_list: "",
              fas_discussion_url: "",
              fas_irc_channels: [],
            },
          }),
        ),
      ),
    );

    const root = buildRoot({
      "data-group-form-api-url": "/api/v1/groups/infra/edit",
      "data-group-form-detail-url": "/group/infra/",
    });

    const app = mountGroupFormPage(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-group-form-vue-root]")).not.toBeNull();
  });
});
