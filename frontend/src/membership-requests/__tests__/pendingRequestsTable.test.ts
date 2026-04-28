import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";

import PendingRequestsTable from "../components/PendingRequestsTable.vue";
import type { MembershipRequestRow, MembershipRequestsBootstrap, PendingFilterOption } from "../types";

const bootstrap: MembershipRequestsBootstrap = {
  clearFilterUrl: "/membership/requests/",
  pendingApiUrl: "/api/v1/membership/requests/pending",
  onHoldApiUrl: "/api/v1/membership/requests/on-hold",
  pendingPageSize: 25,
  onHoldPageSize: 10,
  bulkUrl: "/membership/requests/bulk/",
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
  nextUrl: "/membership/requests/?filter=renewals",
  csrfToken: "csrf-token",
  canRequestInfo: true,
  notesCanView: true,
  notesCanWrite: true,
  notesCanVote: true,
};

const filterOptions: PendingFilterOption[] = [
  { value: "all", label: "All", count: 2 },
  { value: "renewals", label: "Renewals", count: 1 },
];

const row: MembershipRequestRow = {
  request_id: 101,
  status: "pending",
  requested_at: "2026-04-21T12:00:00",
  on_hold_since: null,
  target: {
    kind: "user",
    label: "Alice Example",
    secondary_label: "alice",
    username: "alice",
    deleted: false,
  },
  requested_by: {
    show: true,
    username: "bob",
    full_name: "Bob Reviewer",
    deleted: false,
  },
  membership_type: {
    id: "individual",
    code: "individual",
    name: "Individual",
    category: "individual",
  },
  is_renewal: true,
  responses: [
    {
      question: "Why do you want to renew?",
      answer_html: "Because I am still active.\nStill contributing.",
    },
  ],
};

describe("PendingRequestsTable", () => {
  afterEach(() => {
    window.history.replaceState({}, "", "/");
  });

  function rowCells(wrapper: ReturnType<typeof mount>) {
    return wrapper.get("tbody tr").findAll("td");
  }

  it("renders legacy request metadata, response placement, requester linking, and anchor pagination", () => {
    window.history.replaceState({}, "", "/membership/requests/?filter=renewals&on_hold_page=3");

    const wrapper = mount(PendingRequestsTable, {
      props: {
        bootstrap,
        rows: [row],
        count: 12,
        filterOptions,
        selectedFilter: "renewals",
        currentPage: 6,
        totalPages: 12,
        pageSize: 25,
        isLoading: false,
        error: "",
      },
      global: {
        stubs: {
          MembershipNotesCard: {
            template: '<div data-membership-notes-card-stub="true"></div>',
          },
        },
      },
    });

    const bodyCells = rowCells(wrapper);
    const requestCell = bodyCells[1];
    const requestedForCell = bodyCells[2];
    const typeCell = bodyCells[3];

    expect(requestCell?.classes()).toEqual(expect.arrayContaining(["align-top", "text-muted", "text-nowrap"]));
    expect(requestCell?.attributes("style")).toContain("width: 1%;");
    expect(requestCell?.html()).toContain("<br>");
    expect(requestCell?.text()).toContain("2026-04-21 12:00");
    expect(requestedForCell?.find('a[href="/user/alice/"]').text()).toContain("Alice Example (alice)");
    expect(requestedForCell?.text()).toContain("Requested by: Bob Reviewer (bob)");
    expect(requestedForCell?.find('a[href="/user/bob/"]').text()).toBe("Bob Reviewer (bob)");
    expect(requestedForCell?.find(".mt-1").exists()).toBe(false);
    expect(requestedForCell?.find(".mt-2 [data-membership-notes-card-stub='true']").exists()).toBe(true);
    expect(requestedForCell?.text()).not.toContain("Why do you want to renew?");
    expect(typeCell?.text()).toContain("Individual");
    expect(typeCell?.text()).toContain("Renewal");
    expect(typeCell?.find("details").attributes("open")).toBeDefined();
    expect(typeCell?.find("summary").text()).toBe("Request responses");
    expect(typeCell?.find("[style='white-space: pre-wrap;']").exists()).toBe(true);
    expect(typeCell?.text()).toContain("Still contributing.");
    expect(wrapper.find(".membership-request-actions.membership-request-actions--list").exists()).toBe(true);
    expect(wrapper.find(".badge.badge-primary").text()).toContain("Renewal");
    expect(wrapper.get('button[type="submit"].btn.btn-default').attributes("title")).toBe("Apply selected action to checked requests");
    expect(wrapper.findAll(".pagination .page-link").map((link) => link.text())).toEqual([
      "«",
      "1",
      "…",
      "4",
      "5",
      "6",
      "7",
      "8",
      "…",
      "12",
      "»",
    ]);
    expect(wrapper.findAll(".pagination a.page-link").map((link) => ({
      text: link.text(),
      href: link.attributes("href"),
      ariaLabel: link.attributes("aria-label") || "",
    }))).toEqual([
      { text: "«", href: "/membership/requests/?filter=renewals&pending_page=5&on_hold_page=3", ariaLabel: "Previous" },
      { text: "1", href: "/membership/requests/?filter=renewals&on_hold_page=3", ariaLabel: "" },
      { text: "4", href: "/membership/requests/?filter=renewals&pending_page=4&on_hold_page=3", ariaLabel: "" },
      { text: "5", href: "/membership/requests/?filter=renewals&pending_page=5&on_hold_page=3", ariaLabel: "" },
      { text: "6", href: "/membership/requests/?filter=renewals&pending_page=6&on_hold_page=3", ariaLabel: "" },
      { text: "7", href: "/membership/requests/?filter=renewals&pending_page=7&on_hold_page=3", ariaLabel: "" },
      { text: "8", href: "/membership/requests/?filter=renewals&pending_page=8&on_hold_page=3", ariaLabel: "" },
      { text: "12", href: "/membership/requests/?filter=renewals&pending_page=12&on_hold_page=3", ariaLabel: "" },
      { text: "»", href: "/membership/requests/?filter=renewals&pending_page=7&on_hold_page=3", ariaLabel: "Next" },
    ]);
  });

  it("restores the filtered empty-state clear-filter affordance", () => {
    const wrapper = mount(PendingRequestsTable, {
      props: {
        bootstrap,
        rows: [],
        count: 0,
        filterOptions,
        selectedFilter: "renewals",
        currentPage: 1,
        totalPages: 1,
        pageSize: 25,
        isLoading: false,
        error: "",
      },
      global: {
        stubs: {
          MembershipNotesCard: true,
        },
      },
    });

    expect(wrapper.text()).toContain("No requests match this filter.");
    expect(wrapper.find('a[href="/membership/requests/"]').text()).toContain("Clear filter");
  });

  it("matches the legacy deleted-target rule and keeps deleted organizations unlinked", () => {
    const wrapper = mount(PendingRequestsTable, {
      props: {
        bootstrap,
        rows: [{
          ...row,
          target: {
            kind: "organization",
            label: "Former Org",
            secondary_label: "former@example.com",
            organization_id: 77,
            deleted: true,
          },
        }],
        count: 1,
        filterOptions,
        selectedFilter: "all",
        currentPage: 1,
        totalPages: 1,
        pageSize: 25,
        isLoading: false,
        error: "",
      },
      global: {
        stubs: {
          MembershipNotesCard: true,
        },
      },
    });

    const requestedForCell = rowCells(wrapper)[2];
    expect(requestedForCell?.find('a[href="/organization/77/"]').exists()).toBe(false);
    expect(requestedForCell?.text()).toContain("Former Org");
    expect(requestedForCell?.text()).toContain("(deleted)");
  });

  it("renders loading and error states inside tbody rows", () => {
    const loadingWrapper = mount(PendingRequestsTable, {
      props: {
        bootstrap,
        rows: [],
        count: 0,
        filterOptions,
        selectedFilter: "all",
        currentPage: 1,
        totalPages: 1,
        pageSize: 25,
        isLoading: true,
        error: "",
      },
      global: {
        stubs: {
          MembershipNotesCard: true,
        },
      },
    });

    expect(loadingWrapper.get("tbody td").attributes("colspan")).toBe("5");
    expect(loadingWrapper.get("tbody td").text()).toContain("Loading pending requests...");
    expect(loadingWrapper.find(".alert").exists()).toBe(false);

    const errorWrapper = mount(PendingRequestsTable, {
      props: {
        bootstrap,
        rows: [],
        count: 0,
        filterOptions,
        selectedFilter: "all",
        currentPage: 1,
        totalPages: 1,
        pageSize: 25,
        isLoading: false,
        error: "Failed to load membership requests.",
      },
      global: {
        stubs: {
          MembershipNotesCard: true,
        },
      },
    });

    expect(errorWrapper.get("tbody td").text()).toContain("Failed to load membership requests.");
    expect(errorWrapper.find(".alert").exists()).toBe(false);
  });
});
