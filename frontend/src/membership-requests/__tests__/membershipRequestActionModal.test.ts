import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import MembershipRequestActionModal from "../components/MembershipRequestActionModal.vue";
import type { MembershipRequestActionIntent } from "../types";

const rejectAction: MembershipRequestActionIntent = {
  requestId: 42,
  requestStatus: "pending",
  actionKind: "reject",
  actionUrl: "/api/v1/membership/request/42/reject",
  requestTarget: "alice",
  membershipType: "Individual",
};

describe("MembershipRequestActionModal", () => {
  it("copies the selected reject preset into the textarea", async () => {
    const wrapper = mount(MembershipRequestActionModal, {
      props: {
        action: rejectAction,
        csrfToken: "csrf-token",
      },
    });

    const preset = wrapper.get("#membership-request-action-preset");
    const textarea = wrapper.get("#membership-request-action-text");

    expect(wrapper.text()).toContain("RFI unanswered");
    expect(wrapper.text()).toContain("Embargoed country");

    await preset.setValue(
      "This decision is due to legal requirements that currently prevent the AlmaLinux OS Foundation from approving applications from certain countries.",
    );

    expect((textarea.element as HTMLTextAreaElement).value).toBe(
      "This decision is due to legal requirements that currently prevent the AlmaLinux OS Foundation from approving applications from certain countries.",
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function buttonByText(wrapper: ReturnType<typeof mount>, text: string) {
    return wrapper.findAll("button").find((button) => button.text() === text);
  }

  it("submits the action as a form post when the committee chooses a custom email", async () => {
    const submit = vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(() => undefined);
    const wrapper = mount(MembershipRequestActionModal, {
      props: {
        action: { ...rejectAction, customEmailUrl: "/membership/requests/42/reject/" },
        csrfToken: "csrf-token",
      },
    });

    await wrapper.get("#membership-request-action-text").setValue("  Not eligible  ");
    await buttonByText(wrapper, "Reject with custom email")!.trigger("click");

    expect(submit).toHaveBeenCalledTimes(1);
    const form = wrapper.get('form[action="/membership/requests/42/reject/"]');
    expect(form.attributes("method")).toBe("post");
    const fields = Object.fromEntries(
      form.findAll("input").map((input) => [input.attributes("name"), (input.element as HTMLInputElement).value]),
    );
    expect(fields).toEqual({ csrfmiddlewaretoken: "csrf-token", custom_email: "1", reason: "Not eligible" });
  });

  it("labels the custom-email option for approvals and RFIs", () => {
    for (const [actionKind, label] of [["approve", "Approve with custom email"], ["rfi", "Send RFI with custom email"]] as const) {
      const wrapper = mount(MembershipRequestActionModal, {
        props: {
          action: { ...rejectAction, actionKind, customEmailUrl: `/membership/requests/42/${actionKind}/` },
          csrfToken: "csrf-token",
        },
      });
      expect(buttonByText(wrapper, label)).toBeDefined();
    }
  });

  it("still requires the RFI message before handing off to a custom email", async () => {
    const submit = vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(() => undefined);
    const wrapper = mount(MembershipRequestActionModal, {
      props: {
        action: { ...rejectAction, actionKind: "rfi", customEmailUrl: "/membership/requests/42/rfi/" },
        csrfToken: "csrf-token",
      },
    });

    await buttonByText(wrapper, "Send RFI with custom email")!.trigger("click");

    expect(submit).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("This field is required.");
  });

  it("offers no custom email without a custom-email URL or for bulk actions", () => {
    for (const action of [rejectAction, { ...rejectAction, customEmailUrl: "/membership/requests/42/reject/", requestCount: 3 }]) {
      const wrapper = mount(MembershipRequestActionModal, { props: { action, csrfToken: "csrf-token" } });
      expect(wrapper.text()).not.toContain("with custom email");
    }
  });
});
