import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import MembershipRequestDetailActions from "../components/MembershipRequestDetailActions.vue";

describe("MembershipRequestDetailActions", () => {
  it("renders pending actions, opens a Vue modal, and emits action-success", async () => {
    const wrapper = mount(MembershipRequestDetailActions, {
      props: {
        requestId: 42,
        requestStatus: "pending",
        membershipTypeName: "Sponsor",
        requestTarget: "sponsor@example.com",
        approveUrl: "/api/v1/membership/request/42/approve",
        approveOnHoldUrl: "/api/v1/membership/request/42/approve-on-hold",
        rejectUrl: "/api/v1/membership/request/42/reject",
        rfiUrl: "/api/v1/membership/request/42/rfi",
        ignoreUrl: "/api/v1/membership/request/42/ignore",
        canRequestInfo: true,
        showOnHoldApprove: false,
        csrfToken: "csrf-token",
      },
      global: {
        stubs: {
          MembershipRequestActionModal: {
            props: ["action", "csrfToken"],
            template: '<button class="fake-success" @click="$emit(\'success\', { actionKind: action.actionKind, requestStatus: action.requestStatus })">ok</button>',
          },
        },
      },
    });

    expect(wrapper.text()).toContain("Approve");
    expect(wrapper.text()).toContain("Reject");
    expect(wrapper.text()).toContain("RFI");
    expect(wrapper.text()).toContain("Ignore");

    await wrapper.get("button.btn-success").trigger("click");
    await wrapper.get("button.fake-success").trigger("click");

    const events = wrapper.emitted("action-success") || [];
    expect(events).toHaveLength(1);
    expect(events[0]?.[0]).toMatchObject({ actionKind: "approve", requestStatus: "pending" });
  });

  it("renders on-hold approve button only when override is enabled", () => {
    const disabledWrapper = mount(MembershipRequestDetailActions, {
      props: {
        requestId: 42,
        requestStatus: "on_hold",
        membershipTypeName: "Sponsor",
        requestTarget: "sponsor@example.com",
        approveUrl: "/api/v1/membership/request/42/approve",
        approveOnHoldUrl: "/api/v1/membership/request/42/approve-on-hold",
        rejectUrl: "/api/v1/membership/request/42/reject",
        rfiUrl: "/api/v1/membership/request/42/rfi",
        ignoreUrl: "/api/v1/membership/request/42/ignore",
        canRequestInfo: false,
        showOnHoldApprove: false,
        csrfToken: "csrf-token",
      },
      global: {
        stubs: {
          MembershipRequestActionModal: true,
        },
      },
    });

    expect(disabledWrapper.find("button.btn-success").exists()).toBe(false);

    const enabledWrapper = mount(MembershipRequestDetailActions, {
      props: {
        requestId: 42,
        requestStatus: "on_hold",
        membershipTypeName: "Sponsor",
        requestTarget: "sponsor@example.com",
        approveUrl: "/api/v1/membership/request/42/approve",
        approveOnHoldUrl: "/api/v1/membership/request/42/approve-on-hold",
        rejectUrl: "/api/v1/membership/request/42/reject",
        rfiUrl: "/api/v1/membership/request/42/rfi",
        ignoreUrl: "/api/v1/membership/request/42/ignore",
        canRequestInfo: false,
        showOnHoldApprove: true,
        csrfToken: "csrf-token",
      },
      global: {
        stubs: {
          MembershipRequestActionModal: true,
        },
      },
    });

    expect(enabledWrapper.find("button.btn-success").exists()).toBe(true);
  });

  it("hands each email-sending action its custom-email URL", async () => {
    const wrapper = mount(MembershipRequestDetailActions, {
      props: {
        requestId: 42,
        requestStatus: "pending",
        membershipTypeName: "Sponsor",
        requestTarget: "sponsor@example.com",
        approveUrl: "/api/v1/membership/request/42/approve",
        approveOnHoldUrl: "/api/v1/membership/request/42/approve-on-hold",
        rejectUrl: "/api/v1/membership/request/42/reject",
        rfiUrl: "/api/v1/membership/request/42/rfi",
        ignoreUrl: "/api/v1/membership/request/42/ignore",
        approveCustomEmailUrl: "/membership/requests/42/approve/",
        rejectCustomEmailUrl: "/membership/requests/42/reject/",
        rfiCustomEmailUrl: "/membership/requests/42/rfi/",
        canRequestInfo: true,
        showOnHoldApprove: false,
        csrfToken: "csrf-token",
      },
      global: {
        stubs: {
          MembershipRequestActionModal: {
            props: ["action", "csrfToken"],
            template: '<div class="modal-stub">{{ action ? action.customEmailUrl || "none" : "" }}</div>',
          },
        },
      },
    });

    const expected: Array<[string, string]> = [
      ["Approve", "/membership/requests/42/approve/"],
      ["Reject", "/membership/requests/42/reject/"],
      ["RFI", "/membership/requests/42/rfi/"],
      ["Ignore", "none"],
    ];
    for (const [label, url] of expected) {
      await wrapper.findAll("button").find((button) => button.text() === label)!.trigger("click");
      expect(wrapper.get(".modal-stub").text()).toBe(url);
    }
  });
});
