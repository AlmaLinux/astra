import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";

import IneligibleVoterModal from "../IneligibleVoterModal.vue";
import type { IneligibleVoterModalBootstrap } from "../types";

const bootstrap: IneligibleVoterModalBootstrap = {
  cardId: "ineligible-voters-card",
  detailsJsonId: "ineligible-voter-details",
};

function buildDom(details: object): void {
  document.body.innerHTML = `
    <div id="ineligible-voters-card">
      <a href="/user/bob/">bob</a>
    </div>
    <script id="ineligible-voter-details" type="application/json">${JSON.stringify(details)}</script>
    <div class="modal fade" id="ineligible-voter-modal" aria-hidden="true">
      <div class="modal-content">
        <button type="button" data-dismiss="modal">Close</button>
        <strong class="js-ineligible-username"></strong>
        <div class="js-ineligible-reason"></div>
        <dd class="js-ineligible-term-start"></dd>
        <dd class="js-ineligible-election-start"></dd>
        <dd class="js-ineligible-days-at-start"></dd>
        <dd class="js-ineligible-days-short"></dd>
      </div>
    </div>
  `;
}

describe("IneligibleVoterModal", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("opens the modal with decoded reason text and preserves zero values", async () => {
    buildDom({
      bob: {
        reason: "too_new",
        term_start_date: "2026-02-10",
        election_start_date: "2026-02-10",
        days_at_start: 0,
        days_short: 30,
      },
    });

    mount(IneligibleVoterModal, { props: { bootstrap } });

    const link = document.querySelector<HTMLAnchorElement>('#ineligible-voters-card a[href="/user/bob/"]');
    link?.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));

    const modal = document.getElementById("ineligible-voter-modal");
    expect(modal?.classList.contains("show")).toBe(true);
    expect(document.querySelector(".js-ineligible-username")?.textContent).toBe("bob");
    expect(document.querySelector(".js-ineligible-reason")?.textContent).toBe(
      "Membership or sponsorship is active, but too new at the reference date.",
    );
    expect(document.querySelector(".js-ineligible-days-at-start")?.textContent).toBe("0");
    expect(document.querySelector(".js-ineligible-days-short")?.textContent).toBe("30");
  });

  it("renders empty strings for null numeric detail values", async () => {
    buildDom({
      bob: {
        reason: "expired",
        term_start_date: "2026-02-01",
        election_start_date: "2026-02-10",
        days_at_start: null,
        days_short: null,
      },
    });

    mount(IneligibleVoterModal, { props: { bootstrap } });

    const link = document.querySelector<HTMLAnchorElement>('#ineligible-voters-card a[href="/user/bob/"]');
    link?.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));

    expect(document.querySelector(".js-ineligible-days-at-start")?.textContent).toBe("");
    expect(document.querySelector(".js-ineligible-days-short")?.textContent).toBe("");
  });
});