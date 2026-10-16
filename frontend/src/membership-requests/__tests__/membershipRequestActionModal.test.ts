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

  // Blocked actions stay disabled, with the reason as the tooltip on the button's wrapper.
  function expectBlocked(wrapper: ReturnType<typeof mount>, text: string, reason: string): void {
    const button = buttonByText(wrapper, text)!;
    expect(button.attributes("disabled")).toBeDefined();
    expect(button.element.parentElement?.getAttribute("title")).toBe(reason);
  }

  function expectEnabled(wrapper: ReturnType<typeof mount>, text: string): void {
    expect(buttonByText(wrapper, text)!.attributes("disabled")).toBeUndefined();
  }

  it("keeps an on-hold override disabled until the committee justifies it", async () => {
    const wrapper = mount(MembershipRequestActionModal, {
      props: {
        action: { ...rejectAction, requestStatus: "on_hold", actionKind: "approve_on_hold", actionUrl: "/api/v1/membership/request/42/approve-on-hold" },
        csrfToken: "csrf-token",
      },
    });

    expectBlocked(wrapper, "Approve", "Enter a justification for the override first.");
    await wrapper.get("#membership-request-action-text").setValue("   ");
    expectBlocked(wrapper, "Approve", "Enter a justification for the override first.");

    await wrapper.get("#membership-request-action-text").setValue("Sponsor confirmed by phone");
    expectEnabled(wrapper, "Approve");
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
  });

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
    const wrapper = mount(MembershipRequestActionModal, {
      props: {
        action: { ...rejectAction, actionKind: "rfi", customEmailUrl: "/membership/requests/42/rfi/" },
        csrfToken: "csrf-token",
      },
    });

    expectBlocked(wrapper, "Send RFI", "Enter the RFI message first.");
    expectBlocked(wrapper, "Send RFI with custom email", "Enter the RFI message first.");

    await wrapper.get("#membership-request-action-text").setValue("Please share your mirror URL.");
    expectEnabled(wrapper, "Send RFI");
    expectEnabled(wrapper, "Send RFI with custom email");
  });

  it("offers no custom email without a custom-email URL or for bulk actions", () => {
    for (const action of [rejectAction, { ...rejectAction, customEmailUrl: "/membership/requests/42/reject/", requestCount: 3 }]) {
      const wrapper = mount(MembershipRequestActionModal, { props: { action, csrfToken: "csrf-token" } });
      expect(wrapper.text()).not.toContain("with custom email");
    }
  });

  describe("dues at approval", () => {
    const approveAction: MembershipRequestActionIntent = {
      requestId: 7,
      requestStatus: "pending",
      actionKind: "approve",
      actionUrl: "/api/v1/membership/request/7/approve",
      requestTarget: "Acme Org",
      membershipType: "Gold Sponsor Member",
      customEmailUrl: "/membership/requests/7/approve/",
      dues: { default_usd: 20000, current_usd: 5000 },
    };

    function mockApproveFetch() {
      const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({ ok: true })));
      vi.stubGlobal("fetch", fetchMock);
      return fetchMock;
    }

    function sentBody(fetchMock: ReturnType<typeof mockApproveFetch>): FormData {
      return fetchMock.mock.calls[0]?.[1]?.body as FormData;
    }

    it("keeps a renewal's in-kind discount unless the committee changes it", async () => {
      const fetchMock = mockApproveFetch();
      const wrapper = mount(MembershipRequestActionModal, { props: { action: approveAction, csrfToken: "csrf-token" } });

      expect(wrapper.text()).toContain("Pays $5,000 of $20,000 under an in-kind agreement");
      expect((wrapper.get("#membership-request-action-dues").element as HTMLInputElement).value).toBe("5000");

      await buttonByText(wrapper, "Approve")!.trigger("click");
      await new Promise((resolve) => setTimeout(resolve, 0));

      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(sentBody(fetchMock).has("dues_usd")).toBe(false);
    });

    it("requires a reason when the committee changes the dues", async () => {
      const fetchMock = mockApproveFetch();
      const wrapper = mount(MembershipRequestActionModal, {
        props: { action: { ...approveAction, dues: { default_usd: 20000, current_usd: null } }, csrfToken: "csrf-token" },
      });

      expect((wrapper.get("#membership-request-action-dues").element as HTMLInputElement).value).toBe("");
      await wrapper.get("#membership-request-action-dues").setValue("0");
      expectBlocked(wrapper, "Approve", "Give a reason for the dues change first.");

      await wrapper.get("#membership-request-action-dues-reason").setValue("Mirror bandwidth in kind");
      await buttonByText(wrapper, "Approve")!.trigger("click");
      await new Promise((resolve) => setTimeout(resolve, 0));

      expect(sentBody(fetchMock).get("dues_usd")).toBe("0");
      expect(sentBody(fetchMock).get("dues_reason")).toBe("Mirror bandwidth in kind");
    });

    it("carries changed dues through the custom-email form", async () => {
      vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(() => undefined);
      const wrapper = mount(MembershipRequestActionModal, { props: { action: approveAction, csrfToken: "csrf-token" } });

      await wrapper.get("#membership-request-action-dues").setValue("20000");
      await wrapper.get("#membership-request-action-dues-reason").setValue("In-kind agreement ended");
      await buttonByText(wrapper, "Approve with custom email")!.trigger("click");

      const fields = Object.fromEntries(
        wrapper.get('form[action="/membership/requests/7/approve/"]').findAll("input")
          .map((input) => [input.attributes("name"), (input.element as HTMLInputElement).value]),
      );
      expect(fields).toMatchObject({ dues_usd: "20000", dues_reason: "In-kind agreement ended" });
    });

    it("charges the standard price when the committee empties a renewal's discounted dues", async () => {
      const fetchMock = mockApproveFetch();
      const wrapper = mount(MembershipRequestActionModal, { props: { action: approveAction, csrfToken: "csrf-token" } });

      await wrapper.get("#membership-request-action-dues").setValue("");
      expectBlocked(wrapper, "Approve", "Give a reason for the dues change first.");

      await wrapper.get("#membership-request-action-dues-reason").setValue("In-kind agreement ended");
      await buttonByText(wrapper, "Approve")!.trigger("click");
      await new Promise((resolve) => setTimeout(resolve, 0));

      expect(sentBody(fetchMock).get("dues_usd")).toBe("20000");
    });

    it("refuses dues above the standard price before contacting the server", async () => {
      const wrapper = mount(MembershipRequestActionModal, { props: { action: approveAction, csrfToken: "csrf-token" } });

      await wrapper.get("#membership-request-action-dues").setValue("25000");
      await wrapper.get("#membership-request-action-dues-reason").setValue("Typo");

      expectBlocked(wrapper, "Approve", "The annual dues can't exceed the standard price of $20,000.");
      expectBlocked(wrapper, "Approve with custom email", "The annual dues can't exceed the standard price of $20,000.");
    });

    it("has no dues field for approvers who cannot set dues", () => {
      const { dues: _dues, ...withoutDues } = approveAction;
      const wrapper = mount(MembershipRequestActionModal, { props: { action: withoutDues, csrfToken: "csrf-token" } });

      expect(wrapper.find("#membership-request-action-dues").exists()).toBe(false);
    });
  });
});
