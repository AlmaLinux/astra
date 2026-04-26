export interface VoteBreakdownRow {
  votes: number;
  label: string;
  org_name: string | null;
}

export interface ElectionVoteCandidate {
  id: number;
  username: string;
  label: string;
}

export interface ElectionVotePayload {
  election: {
    id: number;
    name: string;
    start_datetime: string;
    end_datetime: string;
    submit_url: string;
    can_submit_vote: boolean;
    voter_votes: number | null;
  };
  vote_weight_breakdown: VoteBreakdownRow[];
  candidates: ElectionVoteCandidate[];
}

export interface ElectionVoteBootstrap {
  apiUrl: string;
  detailUrlTemplate: string;
  verifyUrl: string;
}

export interface VoteSubmitSuccess {
  ok: true;
  election_id: number;
  email_queued: boolean;
  ballot_hash: string;
  nonce: string;
  previous_chain_hash: string;
  chain_hash: string;
}

export interface VoteSubmitError {
  ok: false;
  error: string;
}

export function readElectionVoteBootstrap(root: HTMLElement): ElectionVoteBootstrap | null {
  const apiUrl = root.dataset.electionVoteApiUrl?.trim() ?? "";
  const detailUrlTemplate = root.dataset.electionVoteDetailUrlTemplate?.trim() ?? "";
  const verifyUrl = root.dataset.electionVoteVerifyUrl?.trim() ?? "";
  if (apiUrl === "" || detailUrlTemplate === "" || verifyUrl === "") {
    return null;
  }

  return { apiUrl, detailUrlTemplate, verifyUrl };
}