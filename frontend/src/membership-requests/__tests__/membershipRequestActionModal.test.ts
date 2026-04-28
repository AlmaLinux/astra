import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

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
});