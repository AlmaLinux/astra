import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

const chatChannelsEditorSource = readFileSync(
  resolve(process.cwd(), "../astra_app/core/static/core/js/chat_channels_editor.js"),
  "utf8",
);

describe("chat_channels_editor", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    delete (window as Window & { ChatChannelsEditor?: unknown }).ChatChannelsEditor;
  });

  it("displays existing default Mattermost channel rows for canonical and legacy stored forms", () => {
    document.body.innerHTML = `
      <form>
        <div id="group-chat-fallback">
          <textarea id="id_fas_irc_channels" name="fas_irc_channels">mattermost://channels/atomicsig
mattermost:/channels/legacy</textarea>
        </div>
        <div
          id="group-chat-widget"
          class="d-none js-chat-channels-editor"
          data-textarea-id="id_fas_irc_channels"
          data-fallback-id="group-chat-fallback"
          data-mattermost-default-server="chat.almalinux.org"
          data-mattermost-default-team="almalinux"
          data-irc-default-server="irc.libera.chat"
          data-matrix-default-server="matrix.org"
        >
          <table><tbody></tbody></table>
          <button type="button" class="js-chat-channels-add">Add channel</button>
        </div>
      </form>
    `;

    window.eval(chatChannelsEditorSource);
    (window as Window & { ChatChannelsEditor: { initAll: (scope?: ParentNode) => void } }).ChatChannelsEditor.initAll(document);

    const values = Array.from(document.querySelectorAll<HTMLInputElement>(".chat-channels-value")).map(
      (input) => input.value,
    );

    expect(values).toEqual(["~atomicsig", "~legacy"]);
  });

  it("does not duplicate rows when initAll runs twice on the same widget root", () => {
    document.body.innerHTML = `
      <form>
        <div id="group-chat-fallback">
          <textarea id="id_fas_irc_channels" name="fas_irc_channels">mattermost://channels/atomicsig</textarea>
        </div>
        <div
          id="group-chat-widget"
          class="d-none js-chat-channels-editor"
          data-textarea-id="id_fas_irc_channels"
          data-fallback-id="group-chat-fallback"
          data-mattermost-default-server="chat.almalinux.org"
          data-mattermost-default-team="almalinux"
          data-irc-default-server="irc.libera.chat"
          data-matrix-default-server="matrix.org"
        >
          <table><tbody></tbody></table>
          <button type="button" class="js-chat-channels-add">Add channel</button>
        </div>
      </form>
    `;

    window.eval(chatChannelsEditorSource);
    const editor = (window as Window & { ChatChannelsEditor: { initAll: (scope?: ParentNode) => void } }).ChatChannelsEditor;

    editor.initAll(document);
    editor.initAll(document);

    expect(document.querySelectorAll(".chat-channels-row")).toHaveLength(1);
    expect(document.querySelectorAll(".chat-channels-value")).toHaveLength(1);
  });
});