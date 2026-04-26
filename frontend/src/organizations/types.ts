export interface OrganizationMembershipBadge {
  label: string;
  class_name: string;
  request_url: string | null;
}

export interface OrganizationCardItem {
  id: number;
  name: string;
  status: string;
  logo_url: string;
  link_to_detail: boolean;
  memberships: OrganizationMembershipBadge[];
}

export interface OrganizationsPagination {
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

export interface OrganizationsCardPayload {
  title: string;
  q: string;
  items: OrganizationCardItem[];
  empty_label: string;
  pagination: OrganizationsPagination;
}

export interface OrganizationsResponse {
  my_organization: OrganizationCardItem | null;
  sponsor_card: OrganizationsCardPayload;
  mirror_card: OrganizationsCardPayload;
}

export interface OrganizationsBootstrap {
  apiUrl: string;
  detailUrlTemplate: string;
  createUrl: string;
}

export interface OrganizationsRouteState {
  pathname: string;
  qSponsor: string;
  qMirror: string;
  pageSponsor: number;
  pageMirror: number;
}

export function readOrganizationsBootstrap(root: HTMLElement): OrganizationsBootstrap | null {
  const apiUrl = String(root.dataset.organizationsApiUrl || "").trim();
  const detailUrlTemplate = String(root.dataset.organizationsDetailUrlTemplate || "").trim();
  const createUrl = String(root.dataset.organizationsCreateUrl || "").trim();
  if (!apiUrl || !detailUrlTemplate || !createUrl) {
    return null;
  }
  return { apiUrl, detailUrlTemplate, createUrl };
}

export function readOrganizationsRouteState(currentUrl: string): OrganizationsRouteState {
  const url = new URL(currentUrl, "https://example.test");
  const pageSponsor = Number.parseInt(url.searchParams.get("page_sponsor") || url.searchParams.get("page") || "1", 10);
  const pageMirror = Number.parseInt(url.searchParams.get("page_mirror") || "1", 10);
  return {
    pathname: url.pathname,
    qSponsor: String(url.searchParams.get("q_sponsor") || url.searchParams.get("q") || ""),
    qMirror: String(url.searchParams.get("q_mirror") || ""),
    pageSponsor: Number.isNaN(pageSponsor) || pageSponsor < 1 ? 1 : pageSponsor,
    pageMirror: Number.isNaN(pageMirror) || pageMirror < 1 ? 1 : pageMirror,
  };
}

export function buildOrganizationsRouteUrl(state: OrganizationsRouteState): string {
  const url = new URL(state.pathname, "https://example.test");
  if (state.qSponsor) {
    url.searchParams.set("q_sponsor", state.qSponsor);
  }
  if (state.qMirror) {
    url.searchParams.set("q_mirror", state.qMirror);
  }
  if (state.pageSponsor > 1) {
    url.searchParams.set("page_sponsor", String(state.pageSponsor));
  }
  if (state.pageMirror > 1) {
    url.searchParams.set("page_mirror", String(state.pageMirror));
  }
  return `${url.pathname}${url.search}`;
}
