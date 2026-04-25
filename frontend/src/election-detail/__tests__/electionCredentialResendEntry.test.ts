import { afterEach, describe, expect, it } from "vitest";

import { mountElectionCredentialResendControls } from "../../entrypoints/electionDetail";

function buildRoot(): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-election-credential-resend-root", "");
  root.setAttribute("data-election-send-mail-credentials-api-url", "/api/v1/elections/1/send-mail-credentials");

  const script = document.createElement("script");
  script.id = "election-eligible-voter-usernames-json";
  script.setAttribute("type", "application/json");
  script.textContent = JSON.stringify(["alice", "bob"]);
  root.appendChild(script);

  document.body.appendChild(root);
  return root;
}

describe("mountElectionCredentialResendControls", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("mounts when required resend bootstrap data exists", () => {
    const root = buildRoot();

    const app = mountElectionCredentialResendControls(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-election-credential-resend-vue-root]")).not.toBeNull();
  });
});