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
    detail_url: string;
    submit_url: string;
    verify_url: string;
    can_submit_vote: boolean;
    voter_votes: number | null;
  };
  vote_weight_breakdown: VoteBreakdownRow[];
  candidates: ElectionVoteCandidate[];
}

export interface ElectionVoteBootstrap {
  apiUrl: string;
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
  if (apiUrl === "") {
    return null;
  }

  return { apiUrl };
}