import { afterEach, describe, expect, it } from "vitest";

import { mountElectionEditController } from "../../entrypoints/electionEdit";

describe("mountElectionEditController", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("mounts when the edit controller root exists", () => {
    const root = document.createElement("div");
    root.setAttribute("data-election-edit-root", "");
    document.body.appendChild(root);

    const app = mountElectionEditController(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-election-edit-vue-root]")).not.toBeNull();
  });
});
