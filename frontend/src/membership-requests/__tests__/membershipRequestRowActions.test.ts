import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import MembershipRequestRowActions from "../components/MembershipRequestRowActions.vue";
import type { DOMWrapper, VueWrapper } from "@vue/test-utils";

import type { MembershipRequestRow, MembershipRequestsBootstrap } from "../types";

const bootstrap: MembershipRequestsBootstrap = {
  clearFilterUrl: "/membership/requests/",
  pendingApiUrl: "/api/v1/membership/requests/pending",
  onHoldApiUrl: "/api/v1/membership/requests/on-hold",
  pendingPageSize: 25,
  onHoldPageSize: 10,
  requestIdSentinel: "123456789",
  requestDetailTemplate: "/membership/request/123456789/",
  approveTemplate: "/membership/requests/123456789/approve/",
  approveOnHoldTemplate: "/membership/requests/123456789/approve-on-hold/",
  rejectTemplate: "/membership/requests/123456789/reject/",
  requestInfoTemplate: "/membership/requests/123456789/rfi/",
  ignoreTemplate: "/membership/requests/123456789/ignore/",
  noteAddTemplate: "/membership/requests/123456789/notes/add/",
  noteSummaryTemplate: "/api/v1/membership/notes/123456789/summary",
  noteDetailTemplate: "/api/v1/membership/notes/123456789",
  userProfileTemplate: "/user/__username__/",
  organizationDetailTemplate: "/organization/123456789/",
  canRequestInfo: true,
  notesCanView: true,
  notesCanWrite: true,
  notesCanVote: true,
};

function buildRow(status: "pending" | "on_hold"): MembershipRequestRow {
  return {
    request_id: 51,
    status,
    requested_at: "2026-04-21T12:00:00",
    on_hold_since: status === "on_hold" ? "2026-04-22T10:00:00" : null,
    target: {
      kind: "organization",
      label: "Acme Org",
      secondary_label: "sponsor@example.com",
      organization_id: 42,
      deleted: false,
    },
    requested_by: {
      show: true,
      username: "bob",
      full_name: "Bob Reviewer",
      deleted: false,
    },
    membership_type: {
      id: "sponsor",
      code: "sponsor",
      name: "Sponsor",
      category: "sponsorship",
    },
    is_renewal: false,
    responses: [],
  };
}

describe("MembershipRequestRowActions", () => {
  function buttonByLabel(wrapper: VueWrapper, label: string): DOMWrapper<Element> {
    const button = wrapper.findAll("button").find((node) => node.text() === label);
    if (!button) {
      throw new Error(`Missing button: ${label}`);
    }
    return button;
  }

  it("emits Vue action payloads for pending-row actions", async () => {
    const wrapper = mount(MembershipRequestRowActions, {
      props: {
        row: buildRow("pending"),
        bootstrap,
      },
    });

    expect(wrapper.attributes("style")).toBeUndefined();

    const buttons = wrapper.findAll("button");
    const approve = buttonByLabel(wrapper, "Approve");
    const reject = buttonByLabel(wrapper, "Reject");
    const rfi = buttonByLabel(wrapper, "RFI");
    const ignore = buttonByLabel(wrapper, "Ignore");

    await approve.trigger("click");
    await reject.trigger("click");
    await rfi.trigger("click");
    await ignore.trigger("click");

    const events = wrapper.emitted("open-action") || [];
    expect(events).toHaveLength(4);
    expect(events[0]?.[0]).toMatchObject({
      actionKind: "approve",
      requestStatus: "pending",
      actionUrl: "/membership/requests/51/approve/",
      membershipType: "Sponsor",
      requestTarget: "sponsor@example.com",
    });
    expect(events[1]?.[0]).toMatchObject({
      actionKind: "reject",
      requestStatus: "pending",
      actionUrl: "/membership/requests/51/reject/",
    });
    expect(events[2]?.[0]).toMatchObject({
      actionKind: "rfi",
      requestStatus: "pending",
      actionUrl: "/membership/requests/51/rfi/",
    });
    expect(events[3]?.[0]).toMatchObject({
      actionKind: "ignore",
      requestStatus: "pending",
      actionUrl: "/membership/requests/51/ignore/",
    });

    expect(approve.attributes("title")).toBe("Approve this request");
    expect(approve.attributes("aria-label")).toBe("Approve");
    expect(reject.attributes("title")).toBe("Reject this request");
    expect(reject.attributes("aria-label")).toBe("Reject");
    expect(rfi.attributes("title")).toBe("Request information and put on hold");
    expect(rfi.attributes("aria-label")).toBe("Request for Information");
    expect(ignore.attributes("title")).toBe("Ignore this request");
    expect(ignore.attributes("aria-label")).toBe("Ignore");

    for (const button of buttons) {
      expect(button.attributes("data-toggle")).toBeUndefined();
      expect(button.attributes("data-target")).toBeUndefined();
    }
  });

  it("emits on-hold approve action payload", async () => {
    const wrapper = mount(MembershipRequestRowActions, {
      props: {
        row: buildRow("on_hold"),
        bootstrap,
      },
    });

    const approve = wrapper.get("button.btn-success");
    await approve.trigger("click");

    const events = wrapper.emitted("open-action") || [];
    expect(events).toHaveLength(1);
    expect(events[0]?.[0]).toMatchObject({
      actionKind: "approve_on_hold",
      requestStatus: "on_hold",
      actionUrl: "/membership/requests/51/approve-on-hold/",
      membershipType: "Sponsor",
      requestTarget: "sponsor@example.com",
    });

    expect(approve.attributes("title")).toBe("Approve this on-hold request with committee override");
    expect(approve.attributes("aria-label")).toBe("Approve");
  });
});