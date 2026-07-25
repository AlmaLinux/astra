// Helpers for reflecting the selected date range in the page URL so a link can
// be shared and the report regenerated automatically on load.

export interface DateRange {
  start: string;
  end: string;
}

function isValidIsoDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return false;
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

/**
 * Parse `start`/`end` from a URL query string. Returns the range only when both
 * are present, valid `YYYY-MM-DD` dates, and start is not after end.
 */
export function parseDateRangeFromSearch(search: string): DateRange | null {
  const params = new URLSearchParams(search);
  const start = params.get("start") ?? "";
  const end = params.get("end") ?? "";
  if (!isValidIsoDate(start) || !isValidIsoDate(end) || start > end) {
    return null;
  }
  return { start, end };
}

/**
 * Build a query string with the given range applied, preserving any other
 * params already present in `existingSearch`.
 */
export function buildDateRangeSearch(start: string, end: string, existingSearch = ""): string {
  const params = new URLSearchParams(existingSearch);
  params.set("start", start);
  params.set("end", end);
  return `?${params.toString()}`;
}
