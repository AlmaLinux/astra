import { describe, expect, it } from "vitest";

import { formatMembershipTimestamp } from "../membershipPresentation";

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

describe("formatMembershipTimestamp", () => {
  it("matches the membership request timestamp format with an explicit UTC offset", () => {
    expect(formatMembershipTimestamp("2026-04-26T10:00:00+00:00")).toBe(expectedLocalTimestamp("2026-04-26T10:00:00+00:00"));
  });

  it("returns an empty string for invalid values", () => {
    expect(formatMembershipTimestamp("not-a-date")).toBe("");
    expect(formatMembershipTimestamp(null)).toBe("");
  });
});