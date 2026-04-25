export interface ElectionAuditBootstrap {
  apiUrl: string;
  summaryApiUrl: string;
  detailUrl: string;
  algorithmUrl: string;
  publicBallotsUrl: string;
  publicAuditUrl: string;
  userProfileUrlTemplate: string;
  name: string;
  status: string;
  startDatetime: string;
  endDatetime: string;
  numberOfSeats: number;
  algorithmName: string;
  algorithmVersion: string;
}

export interface ElectionAuditPagination {
  count: number;
  page: number;
  num_pages: number;
  page_numbers: number[];
  has_previous: boolean;
  has_next: boolean;
  previous_page_number: number | null;
  next_page_number: number | null;
  start_index: number;
  end_index: number;
}

export interface ElectionAuditWinnerItem {
  username: string;
  full_name: string;
}

export interface ElectionAuditRoundRow {
  candidate_id: number;
  candidate_username: string;
  candidate_label: string;
  retained_total: string;
  retention_factor: string;
  is_elected: boolean;
  is_eliminated: boolean;
}

export interface ElectionAuditBallotEntry {
  timestamp: string | null;
  ballot_hash: string;
  supersedes_ballot_hash: string | null;
}

export interface ElectionAuditItem {
  timestamp: string | null;
  event_type: string;
  title: string;
  icon: string;
  icon_bg: string;
  anchor: string | null;
  payload: Record<string, unknown>;
  ballot_date?: string;
  ballots_count?: number;
  first_timestamp?: string | null;
  last_timestamp?: string | null;
  ballots_preview_truncated?: boolean;
  ballots_preview_limit?: number;
  summary_text?: string;
  audit_text?: string;
  round_rows?: ElectionAuditRoundRow[];
  elected_users?: ElectionAuditWinnerItem[];
  ballot_entries?: ElectionAuditBallotEntry[];
}

export interface ElectionAuditLogResponse {
  audit_log: {
    items: ElectionAuditItem[];
    pagination: ElectionAuditPagination;
    jump_links: Array<{ anchor: string; label: string }>;
  };
}

export interface ElectionAuditSummaryResponse {
  summary: {
    ballots_cast: number;
    votes_cast: number;
    quota: number | null;
    empty_seats: number;
    tally_elected_users: ElectionAuditWinnerItem[];
    sankey_flows: unknown[];
    sankey_elected_nodes: string[];
    sankey_eliminated_nodes: string[];
  };
}

export interface ElectionAuditRouteState {
  pathname: string;
  page: number;
}

export function readElectionAuditBootstrap(root: HTMLElement): ElectionAuditBootstrap | null {
  const apiUrl = String(root.dataset.electionAuditLogApiUrl || "").trim();
  const summaryApiUrl = String(root.dataset.electionAuditSummaryApiUrl || "").trim();
  const detailUrl = String(root.dataset.electionAuditDetailUrl || "").trim();
  const algorithmUrl = String(root.dataset.electionAuditAlgorithmUrl || "").trim();
  const publicBallotsUrl = String(root.dataset.electionAuditPublicBallotsUrl || "").trim();
  const publicAuditUrl = String(root.dataset.electionAuditPublicAuditUrl || "").trim();
  const userProfileUrlTemplate = String(root.dataset.electionAuditUserProfileUrlTemplate || "").trim();
  const name = String(root.dataset.electionAuditElectionName || "").trim();
  const status = String(root.dataset.electionAuditElectionStatus || "").trim();
  const startDatetime = String(root.dataset.electionAuditStartDatetime || "").trim();
  const endDatetime = String(root.dataset.electionAuditEndDatetime || "").trim();
  const algorithmName = String(root.dataset.electionAuditAlgorithmName || "").trim();
  const algorithmVersion = String(root.dataset.electionAuditAlgorithmVersion || "").trim();
  const numberOfSeats = Number.parseInt(String(root.dataset.electionAuditNumberOfSeats || "0"), 10);

  if (!apiUrl || !summaryApiUrl || !detailUrl || !algorithmUrl || !publicBallotsUrl || !publicAuditUrl || !userProfileUrlTemplate || !name) {
    return null;
  }

  return {
    apiUrl,
    summaryApiUrl,
    detailUrl,
    algorithmUrl,
    publicBallotsUrl,
    publicAuditUrl,
    userProfileUrlTemplate,
    name,
    status,
    startDatetime,
    endDatetime,
    numberOfSeats: Number.isNaN(numberOfSeats) ? 0 : numberOfSeats,
    algorithmName,
    algorithmVersion,
  };
}

export function readElectionAuditRouteState(currentUrl: string): ElectionAuditRouteState {
  const url = new URL(currentUrl, "https://example.test");
  const page = Number.parseInt(url.searchParams.get("page") || "1", 10);
  return {
    pathname: url.pathname,
    page: Number.isNaN(page) || page < 1 ? 1 : page,
  };
}

export function buildElectionAuditRouteUrl(state: ElectionAuditRouteState): string {
  const url = new URL(state.pathname, "https://example.test");
  if (state.page > 1) {
    url.searchParams.set("page", String(state.page));
  }
  return `${url.pathname}${url.search}`;
}