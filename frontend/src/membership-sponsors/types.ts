export interface MembershipSponsorsBootstrap {
  apiUrl: string;
  pageSize: number;
  initialQ: string;
}

export interface SponsorRow {
  membership_id: number;
  organization: {
    id: number;
    name: string;
    url: string;
  };
  representative: {
    username: string;
    full_name: string;
    display_label: string;
    url: string;
  };
  sponsorship_level: string;
  days_left: number | null;
  is_expiring_soon: boolean;
  expires_display: string;
  expires_at_order: string;
}

export interface SponsorsDataTablesResponse {
  draw: number;
  recordsTotal: number;
  recordsFiltered: number;
  data: SponsorRow[];
}

export interface MembershipSponsorsRouteState {
  pathname: string;
  q: string;
  page: number;
}

function readPositiveInt(value: string | undefined): number | null {
  const parsed = Number.parseInt(value || "", 10);
  if (Number.isNaN(parsed) || parsed < 1) {
    return null;
  }
  return parsed;
}

export function readMembershipSponsorsBootstrap(root: HTMLElement): MembershipSponsorsBootstrap | null {
  const {
    membershipSponsorsApiUrl,
    membershipSponsorsPageSize,
    membershipSponsorsInitialQ,
  } = root.dataset;

  if (!membershipSponsorsApiUrl) {
    return null;
  }

  const pageSize = readPositiveInt(membershipSponsorsPageSize);
  if (pageSize === null) {
    return null;
  }

  return {
    apiUrl: membershipSponsorsApiUrl,
    pageSize,
    initialQ: String(membershipSponsorsInitialQ || ""),
  };
}

export function readMembershipSponsorsRouteState(currentUrl: string): MembershipSponsorsRouteState {
  const url = new URL(currentUrl, "https://example.test");
  const page = Number.parseInt(url.searchParams.get("page") || "1", 10);
  return {
    pathname: url.pathname,
    q: String(url.searchParams.get("q") || ""),
    page: Number.isNaN(page) || page < 1 ? 1 : page,
  };
}

export function buildMembershipSponsorsRouteUrl(state: MembershipSponsorsRouteState): string {
  const url = new URL(state.pathname, "https://example.test");
  if (state.q) {
    url.searchParams.set("q", state.q);
  }
  if (state.page > 1) {
    url.searchParams.set("page", String(state.page));
  }
  return `${url.pathname}${url.search}`;
}
