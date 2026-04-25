export interface ElectionListItem {
  id: number;
  name: string;
  description: string;
  status: string;
  start_datetime: string;
  end_datetime: string;
  detail_url: string;
  edit_url: string | null;
}

export interface ElectionsPagination {
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

export interface ElectionsResponse {
  can_manage_elections: boolean;
  items: ElectionListItem[];
  pagination: ElectionsPagination;
}

export interface ElectionsBootstrap {
  apiUrl: string;
}

export interface ElectionsRouteState {
  pathname: string;
  page: number;
}

export function readElectionsBootstrap(root: HTMLElement): ElectionsBootstrap | null {
  const apiUrl = String(root.dataset.electionsApiUrl || "").trim();
  if (!apiUrl) {
    return null;
  }
  return { apiUrl };
}

export function readElectionsRouteState(currentUrl: string): ElectionsRouteState {
  const url = new URL(currentUrl, "https://example.test");
  const page = Number.parseInt(url.searchParams.get("page") || "1", 10);
  return {
    pathname: url.pathname,
    page: Number.isNaN(page) || page < 1 ? 1 : page,
  };
}

export function buildElectionsRouteUrl(state: ElectionsRouteState): string {
  const url = new URL(state.pathname, "https://example.test");
  if (state.page > 1) {
    url.searchParams.set("page", String(state.page));
  }
  return `${url.pathname}${url.search}`;
}