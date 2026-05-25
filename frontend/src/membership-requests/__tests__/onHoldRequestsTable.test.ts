import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import OnHoldRequestsTable from "../components/OnHoldRequestsTable.vue";
import type { MembershipRequestRow, MembershipRequestsBootstrap } from "../types";

function expectedLocalTimestamp(value: string): string {
  const parsed = new Date(value);
  const year = String(parsed.getFullYear());
  const month = String(parsed.getMonth() + 1).padStart(2, "0");
  const day = String(parsed.getDate()).padStart(2, "0");
  const hour = String(parsed.getHours()).padStart(2, "0");
  const minute = String(parsed.getMinutes()).padStart(2, "0");
  const timezoneOffsetMinutes = -parsed.getTimezoneOffset();
  const offsetSign = timezoneOffsetMinutes >= 0 ? "+" : "-";
  const absoluteOffsetMinutes = Math.abs(timezoneOffsetMinutes);
  const offsetHours = String(Math.floor(absoluteOffsetMinutes / 60)).padStart(2, "0");
  const offsetMinutes = String(absoluteOffsetMinutes % 60).padStart(2, "0");

  return `${year}-${month}-${day} ${hour}:${minute} UTC${offsetSign}${offsetHours}:${offsetMinutes}`;
}

function flushPromises(): Promise<void> {
  return Promise.resolve();
}

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
  nextUrl: "/membership/requests/",
  csrfToken: "csrf-token",
  canRequestInfo: true,
  notesCanView: true,
  notesCanWrite: true,
  notesCanVote: true,
};

const row: MembershipRequestRow = {
  request_id: 88,
  status: "on_hold",
  requested_at: "2026-04-20T08:30:00",
  on_hold_since: "2026-04-21T12:00:00",
  target: {
    kind: "organization",
    label: "Acme Org",
    secondary_label: "sponsor@example.com",
    organization_id: 42,
    deleted: false,
  },
  requested_by: {
    show: true,
    username: "carol",
    full_name: "Carol Example",
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

describe("OnHoldRequestsTable", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-22T12:00:00"));
    window.history.replaceState({}, "", "/membership/requests/?filter=renewals&pending_page=6");
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    window.history.replaceState({}, "", "/");
  });

  function rowCells(wrapper: ReturnType<typeof mount>) {
    return wrapper.get("tbody tr").findAll("td");
  }

  it("renders legacy linking, notes spacing, apply title, and anchor pagination", () => {
    const wrapper = mount(OnHoldRequestsTable, {
      props: {
        bootstrap,
        rows: [row],
        count: 12,
        currentPage: 3,
        totalPages: 6,
        pageSize: 10,
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

    expect(wrapper.text()).toContain("Request #88");
    expect(requestCell?.classes()).toEqual(expect.arrayContaining(["align-top", "text-muted", "text-nowrap"]));
    expect(requestCell?.attributes("style")).toContain("width: 1%;");
    expect(requestCell?.html()).toContain("<br>");
    expect(requestCell?.text()).toContain(expectedLocalTimestamp(row.requested_at));
    expect(requestedForCell?.find('a[href="/organization/42/"]').text()).toContain("Acme Org (sponsor@example.com)");
    expect(requestedForCell?.text()).toContain("Requested by: Carol Example (carol)");
    expect(requestedForCell?.find('a[href="/user/carol/"]').text()).toBe("Carol Example (carol)");
    expect(requestedForCell?.find(".mt-1").exists()).toBe(false);
    expect(requestedForCell?.find(".mt-2 [data-membership-notes-card-stub='true']").exists()).toBe(true);
    expect(wrapper.text()).toContain(expectedLocalTimestamp(row.on_hold_since));
    expect(wrapper.text()).toContain("1 day ago");
    expect(wrapper.find(".membership-request-actions.membership-request-actions--list").exists()).toBe(true);
    expect(wrapper.get('button[type="submit"].btn.btn-default').attributes("title")).toBe("Apply selected action to checked requests");
    expect(wrapper.findAll(".pagination .page-link").map((link) => link.text())).toEqual(["«", "1", "2", "3", "4", "5", "6", "»"]);
    expect(wrapper.findAll(".pagination a.page-link").map((link) => ({
      text: link.text(),
      href: link.attributes("href"),
      ariaLabel: link.attributes("aria-label") || "",
    }))).toEqual([
      { text: "«", href: "/membership/requests/?filter=renewals&pending_page=6&on_hold_page=2", ariaLabel: "Previous" },
      { text: "1", href: "/membership/requests/?filter=renewals&pending_page=6", ariaLabel: "" },
      { text: "2", href: "/membership/requests/?filter=renewals&pending_page=6&on_hold_page=2", ariaLabel: "" },
      { text: "3", href: "/membership/requests/?filter=renewals&pending_page=6&on_hold_page=3", ariaLabel: "" },
      { text: "4", href: "/membership/requests/?filter=renewals&pending_page=6&on_hold_page=4", ariaLabel: "" },
      { text: "5", href: "/membership/requests/?filter=renewals&pending_page=6&on_hold_page=5", ariaLabel: "" },
      { text: "6", href: "/membership/requests/?filter=renewals&pending_page=6&on_hold_page=6", ariaLabel: "" },
      { text: "»", href: "/membership/requests/?filter=renewals&pending_page=6&on_hold_page=4", ariaLabel: "Next" },
    ]);
  });

  it("matches the legacy deleted-target rule for on-hold organizations", () => {
    const wrapper = mount(OnHoldRequestsTable, {
      props: {
        bootstrap,
        rows: [{
          ...row,
          target: {
            kind: "organization",
            label: "Deleted Org",
            secondary_label: "deleted@example.com",
            organization_id: 44,
            deleted: true,
          },
        }],
        count: 1,
        currentPage: 1,
        totalPages: 1,
        pageSize: 10,
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
    expect(requestedForCell?.find('a[href="/organization/44/"]').exists()).toBe(false);
    expect(requestedForCell?.text()).toContain("Deleted Org");
    expect(requestedForCell?.text()).toContain("(deleted)");
  });

  it("renders loading and error states inside tbody rows", () => {
    const loadingWrapper = mount(OnHoldRequestsTable, {
      props: {
        bootstrap,
        rows: [],
        count: 0,
        currentPage: 1,
        totalPages: 1,
        pageSize: 10,
        isLoading: true,
        error: "",
      },
      global: {
        stubs: {
          MembershipNotesCard: true,
        },
      },
    });

    expect(loadingWrapper.get("tbody td").attributes("colspan")).toBe("6");
    expect(loadingWrapper.get("tbody td").text()).toContain("Loading on-hold requests...");
    expect(loadingWrapper.find(".alert").exists()).toBe(false);

    const errorWrapper = mount(OnHoldRequestsTable, {
      props: {
        bootstrap,
        rows: [],
        count: 0,
        currentPage: 1,
        totalPages: 1,
        pageSize: 10,
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

  it("offers the accept bulk action alongside reject and ignore", () => {
    const wrapper = mount(OnHoldRequestsTable, {
      props: {
        bootstrap,
        rows: [row],
        count: 1,
        currentPage: 1,
        totalPages: 1,
        pageSize: 10,
        isLoading: false,
        error: "",
      },
      global: {
        stubs: {
          MembershipNotesCard: true,
        },
      },
    });

    expect(
      wrapper.findAll('select[name="bulk_action"] option').slice(1).map((option) => ({
        value: option.attributes("value"),
        label: option.text(),
      }))
    ).toEqual([
      { value: "accept", label: "Accept" },
      { value: "reject", label: "Reject" },
      { value: "ignore", label: "Ignore" },
    ]);
  });

  it("emits an approve-on-hold bulk intent instead of posting the on-hold accept action to the bulk endpoint", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(OnHoldRequestsTable, {
      props: {
        bootstrap,
        rows: [row],
        count: 1,
        currentPage: 1,
        totalPages: 1,
        pageSize: 10,
        isLoading: false,
        error: "",
      },
      global: {
        stubs: {
          MembershipNotesCard: true,
        },
      },
    });

    await wrapper.get<HTMLInputElement>('tbody input[type="checkbox"][name="selected"][value="88"]').setValue(true);
    await wrapper.get('select[name="bulk_action"]').setValue("accept");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(wrapper.emitted("bulk-approve-on-hold")).toEqual([[{ requestIds: [88] }]]);
  });
});
