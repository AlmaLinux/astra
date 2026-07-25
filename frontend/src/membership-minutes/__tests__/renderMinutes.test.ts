import { describe, expect, it } from "vitest";

import { formatMinutesDate, renderMinutesHtml } from "../renderMinutes";
import type { MinutesData } from "../types";

function emptyData(): MinutesData {
  return {
    start: "2026-10-25",
    end: "2026-11-18",
    summary: { received_or_updated: 0, earlier_pending: 0, rfi: 0, declined: 0, accepted: 0, pending: 0 },
    categories: [
      { code: "individual", rfi: [], declined: [], accepted: [] },
      { code: "mirror", rfi: [], declined: [], accepted: [] },
      { code: "sponsorship", rfi: [], declined: [], accepted: [] },
    ],
  };
}

describe("formatMinutesDate", () => {
  it("formats an ISO date as a long US date without timezone drift", () => {
    expect(formatMinutesDate("2026-10-25")).toBe("October 25, 2026");
    expect(formatMinutesDate("2026-11-18")).toBe("November 18, 2026");
  });
});

describe("renderMinutesHtml", () => {
  it("renders the summary with formatted dates and counts", () => {
    const data = emptyData();
    data.summary = { received_or_updated: 5, earlier_pending: 1, rfi: 2, declined: 2, accepted: 2, pending: 0 };
    const html = renderMinutesHtml(data);
    expect(html).toContain("Summary of requests between October 25, 2026 and November 18, 2026:");
    expect(html).toContain("Number of requests received or updated during this period: 5");
    expect(html).toContain("Number of earlier pending requests: 1");
    expect(html).toContain("Requests still pending: 0");
  });

  it("renders 'None' for empty subsections", () => {
    const html = renderMinutesHtml(emptyData());
    expect(html).toContain("Review existing individual applicants");
    expect((html.match(/<li>None<\/li>/g) ?? []).length).toBe(9);
  });

  it("renders declined requests as links with reasons", () => {
    const data = emptyData();
    data.categories[0].declined = [
      { id: 282, url: "https://accounts.example/membership/request/282/", membership_type_name: "Individual", reason: "Not eligible" },
    ];
    const html = renderMinutesHtml(data);
    expect(html).toContain('<a href="https://accounts.example/membership/request/282/">#282</a>: Not eligible');
  });

  it("appends the membership type name only for sponsors and omits reason on accepted", () => {
    const data = emptyData();
    data.categories[2].accepted = [
      { id: 290, url: "https://accounts.example/membership/request/290/", membership_type_name: "Platinum Sponsor" },
    ];
    data.categories[2].rfi = [
      { id: 293, url: "https://accounts.example/membership/request/293/", membership_type_name: "Gold Sponsor", reason: "Need details" },
    ];
    const html = renderMinutesHtml(data);
    expect(html).toContain('<a href="https://accounts.example/membership/request/290/">#290</a> (Platinum Sponsor)</li>');
    expect(html).toContain('<a href="https://accounts.example/membership/request/293/">#293</a> (Gold Sponsor): Need details');
  });

  it("escapes HTML in reason text", () => {
    const data = emptyData();
    data.categories[1].rfi = [
      { id: 5, url: "https://accounts.example/membership/request/5/", membership_type_name: "Mirror", reason: "<script>bad</script>" },
    ];
    const html = renderMinutesHtml(data);
    expect(html).toContain("&lt;script&gt;bad&lt;/script&gt;");
    expect(html).not.toContain("<script>bad");
  });
});
