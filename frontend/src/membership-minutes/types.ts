export const MINUTES_CATEGORY_CODES = ["individual", "mirror", "sponsorship"] as const;
export type MinutesCategoryCode = (typeof MINUTES_CATEGORY_CODES)[number];

export interface MinutesRequestRecord {
  id: number;
  url: string;
  membership_type_name: string;
  reason?: string;
}

export interface MinutesCategory {
  code: MinutesCategoryCode;
  rfi: MinutesRequestRecord[];
  declined: MinutesRequestRecord[];
  accepted: MinutesRequestRecord[];
}

export interface MinutesSummary {
  received_or_updated: number;
  earlier_pending: number;
  rfi: number;
  declined: number;
  accepted: number;
  pending: number;
}

export interface MinutesData {
  start: string;
  end: string;
  summary: MinutesSummary;
  categories: MinutesCategory[];
}

export interface MembershipMinutesBootstrap {
  apiUrl: string;
}

export function readMembershipMinutesBootstrap(root: HTMLElement): MembershipMinutesBootstrap | null {
  const { membershipMinutesApiUrl } = root.dataset;
  if (!membershipMinutesApiUrl) {
    return null;
  }
  return { apiUrl: membershipMinutesApiUrl };
}
