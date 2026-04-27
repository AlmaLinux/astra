const LONG_MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

function parseDate(value: string | null | undefined): Date | null {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed;
}

export function formatDjangoDateTime(value: string | null): string {
  const parsed = parseDate(value);
  if (!parsed) {
    return "";
  }

  const month = LONG_MONTH_NAMES[parsed.getUTCMonth()] || "";
  const day = parsed.getUTCDate();
  const year = parsed.getUTCFullYear();
  const hour24 = parsed.getUTCHours();
  const minute = parsed.getUTCMinutes();

  let timeLabel = "";
  if (hour24 === 0 && minute === 0) {
    timeLabel = "midnight";
  } else if (hour24 === 12 && minute === 0) {
    timeLabel = "noon";
  } else {
    const hour12 = hour24 % 12 || 12;
    const minuteLabel = minute === 0 ? "" : `:${String(minute).padStart(2, "0")}`;
    const period = hour24 < 12 ? "a.m." : "p.m.";
    timeLabel = `${hour12}${minuteLabel} ${period}`;
  }

  return `${month} ${day}, ${year}, ${timeLabel}`;
}