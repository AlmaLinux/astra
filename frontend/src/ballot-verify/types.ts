export interface BallotVerifyBootstrap {
  apiUrl: string;
  verifyBallotHashUrl: string;
  verifyBallotChainUrl: string;
  verifyAuditLogUrl: string;
  electionDetailUrlTemplate: string;
  auditLogUrlTemplate: string;
}

export interface BallotVerifyElection {
  id: number;
  name: string;
}

export interface BallotVerifyResponse {
  receipt: string;
  has_query: boolean;
  is_valid_receipt: boolean;
  found: boolean;
  election: BallotVerifyElection | null;
  election_status: string;
  submitted_date: string;
  is_superseded: boolean;
  is_final_ballot: boolean;
  public_ballots_url: string;
  rate_limited: boolean;
  verification_snippet: string;
}

export function readBallotVerifyBootstrap(root: HTMLElement): BallotVerifyBootstrap | null {
  const apiUrl = String(root.dataset.ballotVerifyApiUrl || "").trim();
  const verifyBallotHashUrl = String(root.dataset.ballotVerifyHashScriptUrl || "").trim();
  const verifyBallotChainUrl = String(root.dataset.ballotVerifyChainScriptUrl || "").trim();
  const verifyAuditLogUrl = String(root.dataset.ballotVerifyAuditScriptUrl || "").trim();
  const electionDetailUrlTemplate = String(root.dataset.ballotVerifyElectionDetailUrlTemplate || "").trim();
  const auditLogUrlTemplate = String(root.dataset.ballotVerifyAuditLogUrlTemplate || "").trim();

  if (
    !apiUrl
    || !verifyBallotHashUrl
    || !verifyBallotChainUrl
    || !verifyAuditLogUrl
    || !electionDetailUrlTemplate
    || !auditLogUrlTemplate
  ) {
    return null;
  }

  return {
    apiUrl,
    verifyBallotHashUrl,
    verifyBallotChainUrl,
    verifyAuditLogUrl,
    electionDetailUrlTemplate,
    auditLogUrlTemplate,
  };
}

export function readBallotVerifyReceipt(currentUrl: string): string {
  const url = new URL(currentUrl, "https://example.test");
  return (url.searchParams.get("receipt") || "").trim();
}

export function buildBallotVerifyUrl(pathname: string, receipt: string): string {
  const url = new URL(pathname, "https://example.test");
  if (receipt.trim() !== "") {
    url.searchParams.set("receipt", receipt.trim());
  }
  return `${url.pathname}${url.search}`;
}