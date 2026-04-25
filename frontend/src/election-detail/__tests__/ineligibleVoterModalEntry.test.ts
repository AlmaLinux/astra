import { afterEach, describe, expect, it } from "vitest";

import { mountIneligibleVoterModal } from "../../entrypoints/electionDetail";

describe("mountIneligibleVoterModal", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("mounts when the modal bootstrap data exists", () => {
    const root = document.createElement("div");
    root.setAttribute("data-ineligible-voter-modal-root", "");
    root.setAttribute("data-ineligible-voter-card-id", "ineligible-voters-card");
    root.setAttribute("data-ineligible-voter-details-json-id", "ineligible-voter-details");
    document.body.appendChild(root);

    const app = mountIneligibleVoterModal(root);

    expect(app).not.toBeNull();
    expect(root.querySelector("[data-ineligible-voter-modal-vue-root]")).not.toBeNull();
  });
});