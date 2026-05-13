import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import GroupFormPage from "../GroupFormPage.vue";
import type { GroupFormBootstrap } from "../types";

const chatChannelsEditorSource = readFileSync(
  resolve(process.cwd(), "../astra_app/core/static/core/js/chat_channels_editor.js"),
  "utf8",
);

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const bootstrap: GroupFormBootstrap = {
  apiUrl: "/api/v1/groups/infra/edit",
  detailUrl: "/group/infra/",
  chatDefaults: {
    mattermostServer: "chat.almalinux.org",
    mattermostTeam: "almalinux",
    ircServer: "irc.libera.chat",
    matrixServer: "matrix.org",
  },
};

describe("GroupFormPage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    delete (window as Window & { ChatChannelsEditor?: unknown }).ChatChannelsEditor;
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

  it("renders the shared chat channel widget and displays canonical Mattermost values after loading", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          group: {
            cn: "infra",
            description: "Infrastructure",
            fas_url: "https://example.org/group/infra",
            fas_mailing_list: "infra@example.org",
            fas_discussion_url: "https://discussion.example.org/c/infra",
            fas_irc_channels: ["mattermost://channels/atomicsig"],
          },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    window.eval(chatChannelsEditorSource);

    const wrapper = mount(GroupFormPage, {
      props: { bootstrap },
      attachTo: document.body,
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.find(".js-chat-channels-editor").exists()).toBe(true);
    expect(wrapper.get('[data-mattermost-default-server="chat.almalinux.org"]').exists()).toBe(true);
    expect((wrapper.get('textarea[name="fas_irc_channels"]').element as HTMLTextAreaElement).value).toBe(
      "mattermost://channels/atomicsig",
    );
    expect((wrapper.get(".chat-channels-value").element as HTMLInputElement).value).toBe("~atomicsig");
  });

  it("submits widget-edited chat values instead of stale Vue state", async () => {
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
              fas_irc_channels: ["mattermost://channels/atomicsig"],
            },
          }),
        ),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, group: { cn: "infra" } })));
    vi.stubGlobal("fetch", fetchMock);
    window.eval(chatChannelsEditorSource);

    const wrapper = mount(GroupFormPage, {
      props: { bootstrap },
      attachTo: document.body,
    });

    await flushPromises();
    await flushPromises();

    const channelInput = wrapper.get(".chat-channels-value").element as HTMLInputElement;
    channelInput.value = "~sig-core";
    channelInput.dispatchEvent(new Event("input", { bubbles: true }));

    await wrapper.get("form").trigger("submit");
    await flushPromises();
    await flushPromises();

    const putCall = fetchMock.mock.calls[1];
    expect(JSON.parse(String((putCall[1] as RequestInit).body))).toMatchObject({
      fas_irc_channels: "mattermost://channels/sig-core",
    });
  });

  it("keeps chat field errors visible after a failed save while the widget is active", async () => {
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
              fas_irc_channels: ["mattermost://channels/atomicsig"],
            },
          }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: false,
            error: "Unable to save group info right now.",
            errors: {
              fas_irc_channels: ["Enter a valid chat channel."],
            },
          }),
          { status: 400 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    window.eval(chatChannelsEditorSource);

    const wrapper = mount(GroupFormPage, {
      props: { bootstrap },
      attachTo: document.body,
    });

    await flushPromises();
    await flushPromises();

    expect(wrapper.get("#group-chat-channels-fallback").classes()).toContain("d-none");

    await wrapper.get("form").trigger("submit");
    await flushPromises();
    await flushPromises();

    const chatError = wrapper
      .findAll(".invalid-feedback")
      .find((item) => item.text() === "Enter a valid chat channel.");

    expect(chatError).toBeDefined();
    expect(chatError?.element.closest("#group-chat-channels-fallback")).toBeNull();
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
