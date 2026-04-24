import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import GroupFormPage from "../GroupFormPage.vue";
import type { GroupFormBootstrap } from "../types";

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const bootstrap: GroupFormBootstrap = {
  apiUrl: "/api/v1/groups/infra/edit",
  detailUrl: "/group/infra/",
};

describe("GroupFormPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads and renders initial group values", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          group: {
            cn: "infra",
            description: "Infrastructure",
            fas_url: "https://example.org/group/infra",
            fas_mailing_list: "infra@example.org",
            fas_discussion_url: "https://discussion.example.org/c/infra",
            fas_irc_channels: ["irc://#infra", "irc://#infra-dev"],
          },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(GroupFormPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Group info");
    expect((wrapper.get('textarea[name="description"]').element as HTMLTextAreaElement).value).toBe("Infrastructure");
    expect((wrapper.get('textarea[name="fas_irc_channels"]').element as HTMLTextAreaElement).value).toContain("irc://#infra");
  });

  it("submits updates and redirects on success", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            group: {
              cn: "infra",
              description: "Infrastructure",
              fas_url: "",
              fas_mailing_list: "",
              fas_discussion_url: "",
              fas_irc_channels: [],
            },
          }),
        ),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, group: { cn: "infra" } })));
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(GroupFormPage, {
      props: { bootstrap },
    });

    await flushPromises();
    await flushPromises();

    await wrapper.get('textarea[name="description"]').setValue("Updated");
    await wrapper.get('form').trigger("submit");
    await flushPromises();
    await flushPromises();

    const putCall = fetchMock.mock.calls[1];
    expect(String(putCall[0])).toContain("/api/v1/groups/infra/edit");
    expect((putCall[1] as RequestInit).method).toBe("PUT");
  });
});
