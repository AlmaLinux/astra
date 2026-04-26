export interface ElectionsTurnoutReportBootstrap {
  apiUrl: string;
  electionsUrl: string;
  electionDetailUrlTemplate: string;
}

export interface ElectionsTurnoutReportRow {
  election: {
    id: number;
    name: string;
    status: string;
    start_date: string;
  };
  eligible_count: number;
  eligible_weight: number;
  participating_count: number;
  participating_weight: number;
  turnout_count_pct: number;
  turnout_weight_pct: number;
  candidates_count: number;
  seats: number;
  contest_ratio: number;
  credentials_issued: boolean;
}

export interface ElectionsTurnoutReportResponse {
  rows: ElectionsTurnoutReportRow[];
  chart_data: {
    labels: string[];
    count_turnout: number[];
    weight_turnout: number[];
  };
}

export function readElectionsTurnoutReportBootstrap(root: HTMLElement): ElectionsTurnoutReportBootstrap | null {
  const apiUrl = String(root.dataset.electionsTurnoutReportApiUrl || "").trim();
  const electionsUrl = String(root.dataset.electionsTurnoutReportElectionsUrl || "").trim();
  const electionDetailUrlTemplate = String(root.dataset.electionsTurnoutReportElectionDetailUrlTemplate || "").trim();

  if (!apiUrl || !electionsUrl || !electionDetailUrlTemplate) {
    return null;
  }

  return { apiUrl, electionsUrl, electionDetailUrlTemplate };
}