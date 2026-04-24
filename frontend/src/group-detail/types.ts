export interface GroupMemberItem {
  username: string;
  full_name: string;
  avatar_url: string;
  is_leader?: boolean;
}

export interface GroupSponsorItem {
  username: string;
  full_name: string;
  avatar_url: string;
}

export interface GroupSponsorGroupItem {
  kind?: "group";
  cn: string;
}

export interface GroupLeaderUserItem {
  kind: "user";
  username: string;
  full_name: string;
  avatar_url: string;
}

export interface GroupLeaderGroupItem {
  kind: "group";
  cn: string;
}

export type GroupLeaderItem = GroupLeaderGroupItem | GroupLeaderUserItem;

export interface GroupAgreementItem {
  cn: string;
  signed: boolean;
  detail_url: string;
  list_url: string;
}

export interface GroupDetailPagination {
  count: number;
  page: number;
  num_pages: number;
  page_numbers: number[];
  show_first: boolean;
  show_last: boolean;
  has_previous: boolean;
  has_next: boolean;
  previous_page_number: number | null;
  next_page_number: number | null;
  start_index: number;
  end_index: number;
}

export interface GroupMembersPayload {
  q: string;
  items: GroupMemberItem[];
  pagination: GroupDetailPagination;
}

export interface GroupLeadersPayload {
  items: GroupLeaderItem[];
  pagination: GroupDetailPagination;
}

export interface GroupInfoPayload {
  cn: string;
  description: string;
  fas_url: string;
  fas_mailing_list: string;
  fas_discussion_url: string;
  fas_irc_channels: string[];
  member_count: number;
  is_member: boolean;
  is_sponsor: boolean;
  required_agreements: GroupAgreementItem[];
  unsigned_usernames: string[];
  edit_url: string;
}

export interface GroupInfoResponse {
  group: GroupInfoPayload;
}

export interface GroupLeadersResponse {
  leaders: GroupLeadersPayload;
}

export interface GroupMembersResponse {
  members: GroupMembersPayload;
}

export interface GroupDetailBootstrap {
  infoApiUrl: string;
  leadersApiUrl: string;
  membersApiUrl: string;
  actionUrl: string;
  currentUsername: string;
}

export interface GroupDetailRouteState {
  pathname: string;
  q: string;
  page: number;
  leadersPage: number;
}

export function readGroupDetailBootstrap(root: HTMLElement): GroupDetailBootstrap | null {
  const infoApiUrl = String(root.dataset.groupDetailInfoApiUrl || "").trim();
  const leadersApiUrl = String(root.dataset.groupDetailLeadersApiUrl || "").trim();
  const membersApiUrl = String(root.dataset.groupDetailMembersApiUrl || "").trim();
  const actionUrl = String(root.dataset.groupDetailActionUrl || "").trim();
  const currentUsername = String(root.dataset.groupDetailCurrentUsername || "").trim();
  if (!infoApiUrl || !leadersApiUrl || !membersApiUrl || !actionUrl) {
    return null;
  }
  return { infoApiUrl, leadersApiUrl, membersApiUrl, actionUrl, currentUsername };
}

export function readGroupDetailRouteState(currentUrl: string): GroupDetailRouteState {
  const url = new URL(currentUrl, "https://example.test");
  const page = Number.parseInt(url.searchParams.get("page") || "1", 10);
  const leadersPage = Number.parseInt(url.searchParams.get("leaders_page") || "1", 10);
  return {
    pathname: url.pathname,
    q: String(url.searchParams.get("q") || ""),
    page: Number.isNaN(page) || page < 1 ? 1 : page,
    leadersPage: Number.isNaN(leadersPage) || leadersPage < 1 ? 1 : leadersPage,
  };
}

export function buildGroupDetailRouteUrl(state: GroupDetailRouteState): string {
  const url = new URL(state.pathname, "https://example.test");
  if (state.q) {
    url.searchParams.set("q", state.q);
  }
  if (state.page > 1) {
    url.searchParams.set("page", String(state.page));
  }
  if (state.leadersPage > 1) {
    url.searchParams.set("leaders_page", String(state.leadersPage));
  }
  return `${url.pathname}${url.search}`;
}
