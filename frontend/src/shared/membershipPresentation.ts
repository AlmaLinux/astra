const MEMBERSHIP_TIER_CLASSES: Record<string, string> = {
  platinum: "membership-platinum",
  gold: "membership-gold",
  silver: "membership-silver",
  ruby: "membership-ruby",
};

function parseDate(value: string | null): Date | null {
  if (!value) {
    return null;
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }

  return parsed;
}

function safeTimeZone(timezoneName: string): string {
  return String(timezoneName || "").trim() || "UTC";
}

function dateTimeParts(value: Date, timezoneName: string, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormatPart[] {
  return new Intl.DateTimeFormat("en-US", {
    ...options,
    timeZone: safeTimeZone(timezoneName),
  }).formatToParts(value);
}

function partValue(parts: Intl.DateTimeFormatPart[], type: Intl.DateTimeFormatPartTypes): string {
  return parts.find((part) => part.type === type)?.value || "";
}

export function membershipTierClass(code: string): string {
  return MEMBERSHIP_TIER_CLASSES[String(code || "").trim().toLowerCase()] || "membership-standard";
}

export function formatMonthYear(value: string | null): string {
  const parsed = parseDate(value);
  if (!parsed) {
    return "";
  }

  return new Intl.DateTimeFormat("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(parsed);
}

export function formatShortDate(value: string | null): string {
  const parsed = parseDate(value);
  if (!parsed) {
    return "";
  }

  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(parsed);
}

export function formatPreciseDateTime(value: string | null, timezoneName: string): string {
  const parsed = parseDate(value);
  if (!parsed) {
    return "";
  }

  const parts = dateTimeParts(parsed, timezoneName, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  const timeZoneLabel = safeTimeZone(timezoneName);
  const month = partValue(parts, "month");
  const day = partValue(parts, "day");
  const year = partValue(parts, "year");
  const hour = partValue(parts, "hour");
  const minute = partValue(parts, "minute");

  return `${month} ${day}, ${year} ${hour}:${minute} (${timeZoneLabel})`;
}

export function formatFixedCurrentTime(date: Date, timezoneName: string): string {
  const parts = dateTimeParts(date, timezoneName, {
    weekday: "long",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  const weekday = partValue(parts, "weekday");
  const hour = partValue(parts, "hour");
  const minute = partValue(parts, "minute");
  const second = partValue(parts, "second");

  return `${weekday} ${hour}:${minute}:${second}`;
}

export function formatDateInputValue(value: string | null): string {
  const parsed = parseDate(value);
  if (!parsed) {
    return "";
  }

  return parsed.toISOString().slice(0, 10);
}

export function pendingMembershipBadge(status: string, isOwner: boolean): { label: string; className: string } {
  const normalizedStatus = String(status || "").trim().toLowerCase();
  const isOnHold = normalizedStatus === "on_hold";
  const label = isOnHold ? (isOwner ? "Action required" : "On hold") : "Under review";
  const statusClass = isOnHold ? "alx-status-badge--action" : "alx-status-badge--review";
  const legacyClass = isOnHold ? "membership-action-required" : "membership-under-review";

  return {
    label,
    className: `badge ${legacyClass} alx-status-badge ${statusClass}`,
  };
}