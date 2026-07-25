// ---------------------------------------------------------------------------
// Minutes template.
//
// This file owns ALL wording and formatting of the generated minutes. Tweak the
// strings / structure here to change how the minutes read; nothing else needs to
// change. The output is real HTML (nested <ul> lists + links) so it can be
// pasted straight into Google Docs with formatting preserved.
// ---------------------------------------------------------------------------

import type { MinutesCategory, MinutesCategoryCode, MinutesData, MinutesRequestRecord } from "./types";

interface SectionConfig {
  heading: string;
  // Whether to append the membership type name in parentheses after each request
  // (used for sponsor tiers, e.g. "(Gold Sponsor)").
  showTypeName: boolean;
}

const SECTION_CONFIG: Record<MinutesCategoryCode, SectionConfig> = {
  individual: {
    heading:
      "Review existing individual applicants, address any concerns, and vote to approve any unanimous selections.",
    showTypeName: false,
  },
  mirror: {
    heading: "Review & approve existing mirror applications and address any concerns as needed.",
    showTypeName: false,
  },
  sponsorship: {
    heading: "Review pending sponsor applications.",
    showTypeName: true,
  },
};

const SUBSECTION_LABELS = {
  rfi: "Further information requested:",
  declined: "Declined requests:",
  accepted: "Accepted requests:",
} as const;

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function formatMinutesDate(isoDate: string): string {
  const parsed = new Date(`${isoDate}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) {
    return isoDate;
  }
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  }).format(parsed);
}

function renderRequestItem(
  record: MinutesRequestRecord,
  { showTypeName, showReason }: { showTypeName: boolean; showReason: boolean },
): string {
  let content = `<a href="${escapeHtml(record.url)}">#${record.id}</a>`;
  if (showTypeName && record.membership_type_name) {
    content += ` (${escapeHtml(record.membership_type_name)})`;
  }
  const reason = (record.reason ?? "").trim();
  if (showReason && reason) {
    content += `: ${escapeHtml(reason)}`;
  }
  return `<li>${content}</li>`;
}

function renderRequestList(
  records: MinutesRequestRecord[],
  options: { showTypeName: boolean; showReason: boolean },
): string {
  if (records.length === 0) {
    return "<ul><li>None</li></ul>";
  }
  return `<ul>${records.map((record) => renderRequestItem(record, options)).join("")}</ul>`;
}

function renderSection(category: MinutesCategory): string {
  const config = SECTION_CONFIG[category.code];
  const showTypeName = config.showTypeName;
  const body =
    `<li>${SUBSECTION_LABELS.rfi}${renderRequestList(category.rfi, { showTypeName, showReason: true })}</li>` +
    `<li>${SUBSECTION_LABELS.declined}${renderRequestList(category.declined, { showTypeName, showReason: true })}</li>` +
    `<li>${SUBSECTION_LABELS.accepted}${renderRequestList(category.accepted, { showTypeName, showReason: false })}</li>`;
  return `<li>${escapeHtml(config.heading)}<ul>${body}</ul></li>`;
}

export function renderMinutesHtml(data: MinutesData): string {
  const { summary } = data;
  const startLabel = formatMinutesDate(data.start);
  const endLabel = formatMinutesDate(data.end);

  const summarySection =
    `<li>Summary of requests between ${escapeHtml(startLabel)} and ${escapeHtml(endLabel)}:<ul>` +
    `<li>Number of requests received or updated during this period: ${summary.received_or_updated}</li>` +
    `<li>Number of earlier pending requests: ${summary.earlier_pending}</li>` +
    `<li>Outcome of requests:<ul>` +
    `<li>Further information requested: ${summary.rfi}</li>` +
    `<li>Requests declined: ${summary.declined}</li>` +
    `<li>Requests accepted: ${summary.accepted}</li>` +
    `<li>Requests still pending: ${summary.pending}</li>` +
    `</ul></li>` +
    `</ul></li>`;

  const sections = data.categories.map((category) => renderSection(category)).join("");

  return `<ul>${summarySection}${sections}</ul>`;
}
