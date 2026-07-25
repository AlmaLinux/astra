import { describe, expect, it } from "vitest";

import { buildDateRangeSearch, parseDateRangeFromSearch } from "../urlState";

describe("parseDateRangeFromSearch", () => {
  it("returns the range when both dates are valid", () => {
    expect(parseDateRangeFromSearch("?start=2026-10-25&end=2026-11-18")).toEqual({
      start: "2026-10-25",
      end: "2026-11-18",
    });
  });

  it("returns null when a date is missing", () => {
    expect(parseDateRangeFromSearch("?start=2026-10-25")).toBeNull();
    expect(parseDateRangeFromSearch("")).toBeNull();
  });

  it("returns null for malformed or invalid dates", () => {
    expect(parseDateRangeFromSearch("?start=2026-13-01&end=2026-11-18")).toBeNull();
    expect(parseDateRangeFromSearch("?start=oct&end=2026-11-18")).toBeNull();
    expect(parseDateRangeFromSearch("?start=2026-2-3&end=2026-11-18")).toBeNull();
  });

  it("returns null when start is after end", () => {
    expect(parseDateRangeFromSearch("?start=2026-11-19&end=2026-11-18")).toBeNull();
  });
});

describe("buildDateRangeSearch", () => {
  it("builds a query string with the range", () => {
    expect(buildDateRangeSearch("2026-10-25", "2026-11-18")).toBe("?start=2026-10-25&end=2026-11-18");
  });

  it("overwrites existing range params while preserving others", () => {
    const result = buildDateRangeSearch("2026-10-25", "2026-11-18", "?start=2000-01-01&foo=bar");
    const params = new URLSearchParams(result);
    expect(params.get("start")).toBe("2026-10-25");
    expect(params.get("end")).toBe("2026-11-18");
    expect(params.get("foo")).toBe("bar");
  });
});
