import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import MembershipManageModal from "../MembershipManageModal.vue";

const baseProps = {
  modalId: "manage-gold",
  membershipType: { name: "Gold Sponsor Member", annualDuesUsd: 20000 },
  targetName: "Acme Org",
  targetNoun: "organization name",
  expiresAt: "2026-11-18T23:59:59Z",
  expiresLabel: "November 18, 2026",
  expiryMinDate: "2026-04-01",
  updateUrl: "/membership/7/update/",
  terminateUrl: "/membership/7/terminate/",
  csrfToken: "csrf-token",
  nextUrl: "/organization/1/",
};

function mountModal(props: Partial<typeof baseProps> & { duesUsd?: number | null } = {}) {
  return mount(MembershipManageModal, { props: { ...baseProps, duesUsd: null, ...props } });
}

function saveForm(wrapper: ReturnType<typeof mountModal>) {
  return wrapper.get(`form[action="${baseProps.updateUrl}"]`);
}

function saveButton(wrapper: ReturnType<typeof mountModal>) {
  const button = saveForm(wrapper).get("button[type=submit]");
  return { button, title: () => button.element.parentElement?.getAttribute("title") };
}

describe("MembershipManageModal", () => {
  it("edits the expiration and dues, each with its own reason, in one form with one save button", () => {
    const wrapper = mountModal({ duesUsd: 5000 });

    const form = saveForm(wrapper);
    expect(form.get<HTMLInputElement>('input[name="expires_on"]').element.value).toBe("2026-11-18");
    expect(form.get<HTMLInputElement>('input[name="dues_usd"]').element.value).toBe("5000");
    expect(form.findAll("textarea").map((textarea) => textarea.attributes("name"))).toEqual(["expiration_reason", "dues_reason"]);
    expect(form.find('input[name="csrfmiddlewaretoken"]').attributes("value")).toBe("csrf-token");
    expect(form.find('input[name="next"]').attributes("value")).toBe("/organization/1/");
    expect(wrapper.findAll(`form[action="${baseProps.updateUrl}"]`)).toHaveLength(1);
    expect(form.findAll("button[type=submit]").map((button) => button.text())).toEqual(["Save changes"]);
    expect(wrapper.text()).toContain("Manage membership: Gold Sponsor Member for Acme Org");
    expect(wrapper.text()).toContain("Current expiration: November 18, 2026");
  });

  it("leaves the dues field empty for a sponsor paying the standard price", () => {
    for (const duesUsd of [null, 20000]) {
      const wrapper = mountModal({ duesUsd });
      expect(wrapper.get<HTMLInputElement>('input[name="dues_usd"]').element.value).toBe("");
    }
  });

  it("only shows the dues field for paid tiers that can have their dues changed", () => {
    const freeTier = mountModal({ membershipType: { name: "Community", annualDuesUsd: 0 }, duesUsd: 0 });
    expect(freeTier.find('input[name="dues_usd"]').exists()).toBe(false);
    expect(freeTier.find('textarea[name="dues_reason"]').exists()).toBe(false);

    // Individual memberships have no dues to waive, so their pages pass no dues.
    const individual = mount(MembershipManageModal, {
      props: { ...baseProps, membershipType: { name: "Individual", annualDuesUsd: 0 } },
    });
    expect(individual.find('input[name="dues_usd"]').exists()).toBe(false);
  });

  it("keeps the save button disabled until something changes", async () => {
    const wrapper = mountModal();
    const save = saveButton(wrapper);

    expect(save.button.attributes("disabled")).toBeDefined();
    expect(save.title()).toBe("Change the expiration date or the dues first.");

    await saveForm(wrapper).get('input[name="expires_on"]').setValue("2027-01-31");
    expect(save.button.attributes("disabled")).toBeUndefined();
    expect(save.title()).toBe("Save the changes to this membership");

    await saveForm(wrapper).get('input[name="expires_on"]').setValue("2026-11-18");
    expect(save.button.attributes("disabled")).toBeDefined();
  });

  it("names only the expiration date as changeable when there are no dues", () => {
    const wrapper = mountModal({ membershipType: { name: "Community", annualDuesUsd: 0 } });

    expect(saveButton(wrapper).title()).toBe("Change the expiration date first.");
  });

  it("treats typing the standard price as no dues change", async () => {
    const wrapper = mountModal();

    await saveForm(wrapper).get('input[name="dues_usd"]').setValue("20000");

    expect(saveButton(wrapper).title()).toBe("Change the expiration date or the dues first.");
  });

  it("needs a reason for a dues change, but not for an expiration change", async () => {
    const wrapper = mountModal();
    const form = saveForm(wrapper);
    const save = saveButton(wrapper);

    await form.get('input[name="expires_on"]').setValue("2027-01-31");
    expect(form.get("textarea[name=expiration_reason]").attributes("required")).toBeUndefined();
    expect(save.button.attributes("disabled")).toBeUndefined();

    await form.get('input[name="dues_usd"]').setValue("5000");
    expect(form.get("textarea[name=dues_reason]").attributes("required")).toBeDefined();
    expect(save.title()).toBe("Enter a reason for the dues change first.");

    // The expiration change's reason doesn't explain the dues change.
    await form.get("textarea[name=expiration_reason]").setValue("Paid late");
    expect(save.button.attributes("disabled")).toBeDefined();

    await form.get("textarea[name=dues_reason]").setValue("   ");
    expect(save.button.attributes("disabled")).toBeDefined();

    await form.get("textarea[name=dues_reason]").setValue("Hosts our CI runners");
    expect(save.button.attributes("disabled")).toBeUndefined();
    // Typing the reason re-renders the form; the values entered must survive it.
    expect(form.get<HTMLInputElement>('input[name="dues_usd"]').element.value).toBe("5000");
    expect(form.get<HTMLInputElement>('input[name="expires_on"]').element.value).toBe("2027-01-31");
  });

  it("keeps the save button disabled while the dues exceed the standard price", async () => {
    const wrapper = mountModal();
    const form = saveForm(wrapper);

    await form.get('input[name="dues_usd"]').setValue("25000");
    await form.get("textarea[name=dues_reason]").setValue("Typo");

    expect(saveButton(wrapper).button.attributes("disabled")).toBeDefined();
    expect(saveButton(wrapper).title()).toBe("The annual dues can't exceed the standard price of $20,000.");
  });

  it("keeps the save button disabled without an expiration date", async () => {
    const wrapper = mountModal();

    await saveForm(wrapper).get('input[name="expires_on"]').setValue("");

    expect(saveButton(wrapper).title()).toBe("Enter an expiration date first.");
  });

  it("terminates from a separate form once the target's name is typed", async () => {
    const wrapper = mountModal();
    const form = wrapper.get(`form[action="${baseProps.terminateUrl}"]`);
    const terminate = form.get("button[type=submit]");

    expect(terminate.attributes("disabled")).toBeDefined();
    expect(terminate.element.parentElement?.getAttribute("title")).toBe("Type the organization name to confirm first.");

    await form.get('input[name="confirm"]').setValue("acme org");
    expect(terminate.attributes("disabled")).toBeUndefined();
    expect(terminate.element.parentElement?.getAttribute("title")).toBe("Terminate this membership now");

    await form.get('[data-terminate-action="cancel"]').trigger("click");
    expect(form.get<HTMLInputElement>('input[name="confirm"]').element.value).toBe("");
  });
});
