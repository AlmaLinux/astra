export interface GroupListItem {
  cn: string;
  description: string;
  member_count: number;
}

export interface GroupsPagination {
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

export interface GroupsResponse {
  q: string;
  items: GroupListItem[];
  pagination: GroupsPagination;
}

export interface GroupsBootstrap {
  apiUrl: string;
  detailUrlTemplate: string;
}

export interface GroupsRouteState {
  pathname: string;
  q: string;
  page: number;
}

export function readGroupsBootstrap(root: HTMLElement): GroupsBootstrap | null {
  const apiUrl = String(root.dataset.groupsApiUrl || "").trim();
  const detailUrlTemplate = String(root.dataset.groupsDetailUrlTemplate || "").trim();
  if (!apiUrl || !detailUrlTemplate) {
    return null;
  }
  return { apiUrl, detailUrlTemplate };
}

export function readGroupsRouteState(currentUrl: string): GroupsRouteState {
  const url = new URL(currentUrl, "https://example.test");
  const page = Number.parseInt(url.searchParams.get("page") || "1", 10);
  return {
    pathname: url.pathname,
    q: String(url.searchParams.get("q") || ""),
    page: Number.isNaN(page) || page < 1 ? 1 : page,
  };
}

export function buildGroupsRouteUrl(state: GroupsRouteState): string {
  const url = new URL(state.pathname, "https://example.test");
  if (state.q) {
    url.searchParams.set("q", state.q);
  }
  if (state.page > 1) {
    url.searchParams.set("page", String(state.page));
  }
  return `${url.pathname}${url.search}`;
}
