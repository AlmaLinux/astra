import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import EmailTemplatesPage from "../EmailTemplatesPage.vue";
import type { EmailTemplatesBootstrap } from "../types";

const bootstrap: EmailTemplatesBootstrap = {
  apiUrl: "/api/v1/email-tools/templates/detail",
  createUrl: "/email-tools/templates/new/",
  editUrlTemplate: "/email-tools/templates/__template_id__/",
  deleteUrlTemplate: "/email-tools/templates/__template_id__/delete/",
  csrfToken: "csrf-token",
  initialPayload: {
    templates: [
      { id: 1, name: "editable-template", description: "Editable", isLocked: false },
      { id: 2, name: "locked-template", description: "Locked", isLocked: true },
    ],
  },
};

describe("EmailTemplatesPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the templates table from the data-only payload", () => {
    const wrapper = mount(EmailTemplatesPage, {
      props: { bootstrap },
    });

    expect(wrapper.text()).toContain("Email Templates");
    expect(wrapper.find('a[href="/email-tools/templates/new/"]').text()).toContain("New template");
    expect(wrapper.find('a[href="/email-tools/templates/1/"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("editable-template");
    expect(wrapper.text()).toContain("Locked");
    expect(wrapper.find('button[disabled][aria-disabled="true"]').exists()).toBe(true);
  });

  it("loads from the canonical endpoint when no initial payload is embedded", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      templates: [{ id: 3, name: "fetched-template", description: "Fetched", is_locked: false }],
    })));
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(EmailTemplatesPage, {
      props: {
        bootstrap: {
          ...bootstrap,
          initialPayload: null,
        },
      },
    });

    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/email-tools/templates/detail",
      expect.objectContaining({ credentials: "same-origin" }),
    );
    expect(wrapper.text()).toContain("fetched-template");
  });
});