import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import MembershipRequestRowActions from "../components/MembershipRequestRowActions.vue";
import type { MembershipRequestRow, MembershipRequestsBootstrap } from "../types";

const bootstrap: MembershipRequestsBootstrap = {
  clearFilterUrl: "/membership/requests/",
  pendingApiUrl: "/api/v1/membership/requests/pending",
  onHoldApiUrl: "/api/v1/membership/requests/on-hold",
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
  it("matches legacy titles, aria labels, and request-target modal context", () => {
    const wrapper = mount(MembershipRequestRowActions, {
      props: {
        row: buildRow("pending"),
        bootstrap,
      },
    });

    expect(wrapper.attributes("style")).toBeUndefined();

    const approve = wrapper.get('button[data-target="#shared-approve-modal"]');
    const reject = wrapper.get('button[data-target="#shared-reject-modal"]');
    const rfi = wrapper.get('button[data-target="#shared-rfi-modal"]');
    const ignore = wrapper.get('button[data-target="#shared-ignore-modal"]');

    expect(approve.attributes("title")).toBe("Approve this request");
    expect(approve.attributes("aria-label")).toBe("Approve");
    expect(approve.attributes("data-request-target")).toBe("sponsor@example.com");
    expect(approve.attributes("data-body-emphasis")).toBe("sponsor@example.com");
    expect(reject.attributes("title")).toBe("Reject this request");
    expect(reject.attributes("aria-label")).toBe("Reject");
    expect(rfi.attributes("title")).toBe("Request information and put on hold");
    expect(rfi.attributes("aria-label")).toBe("Request for Information");
    expect(ignore.attributes("title")).toBe("Ignore this request");
    expect(ignore.attributes("aria-label")).toBe("Ignore");
    expect(ignore.attributes("data-body-emphasis")).toBe("sponsor@example.com");
  });

  it("uses the legacy on-hold approve help text", () => {
    const wrapper = mount(MembershipRequestRowActions, {
      props: {
        row: buildRow("on_hold"),
        bootstrap,
      },
    });

    const approve = wrapper.get('button[data-target="#shared-approve-on-hold-modal"]');
    expect(approve.attributes("title")).toBe("Approve this on-hold request with committee override");
    expect(approve.attributes("aria-label")).toBe("Approve");
    expect(approve.attributes("data-request-target")).toBe("sponsor@example.com");
  });
});