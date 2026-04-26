import type { ElectionsPagination } from "../elections/types";

export interface ElectionWinnerItem {
  username: string;
  full_name: string;
}

export interface ElectionTurnoutStats {
  participating_voter_count: number;
  participating_vote_weight_total: number;
  eligible_voter_count: number;
  eligible_vote_weight_total: number;
  required_participating_voter_count: number;
  required_participating_vote_weight_total: number;
  quorum_met: boolean;
  quorum_percent: number;
  quorum_required: boolean;
  participating_voter_percent: number;
  participating_vote_weight_percent: number;
}

export interface ElectionTurnoutChartData {
  labels: string[];
  counts: number[];
}

export interface ElectionInfoPayload {
  id: number;
  name: string;
  description: string;
  url: string;
  status: string;
  start_datetime: string;
  end_datetime: string;
  start_datetime_display: string;
  end_datetime_display: string;
  number_of_seats: number;
  quorum: number;
  eligible_group_cn: string;
  can_vote: boolean;
  viewer_email: string | null;
  credential_issued_at: string | null;
  eligibility_min_membership_age_days: number;
  show_turnout_chart: boolean;
  turnout_stats: ElectionTurnoutStats;
  turnout_chart_data: ElectionTurnoutChartData;
  exclusion_group_messages: string[];
  election_is_finished: boolean;
  tally_winners: ElectionWinnerItem[];
  empty_seats: number;
}

export interface ElectionInfoResponse {
  election: ElectionInfoPayload;
}

export interface ElectionCandidateItem {
  id: number;
  username: string;
  has_user: boolean;
  full_name: string;
  avatar_url: string;
  description: string;
  url: string;
  nominated_by: string;
  nominator_display_name: string;
  nominator_profile_username: string | null;
}

export interface ElectionCandidatesResponse {
  candidates: {
    items: ElectionCandidateItem[];
    pagination: ElectionsPagination;
  };
}

export interface EligibleVoterItem {
  username: string;
  full_name: string;
  avatar_url: string;
}

export interface IneligibleVoterDetails {
  reason: string;
  term_start_date: string;
  election_start_date: string;
  days_at_start: number | null;
  days_short: number | null;
}

export interface EligibleVotersResponse {
  eligible_voters: {
    items: EligibleVoterItem[];
    usernames: string[];
    pagination: ElectionsPagination;
  };
}

export interface IneligibleVotersResponse {
  ineligible_voters: {
    items: EligibleVoterItem[];
    details_by_username: Record<string, IneligibleVoterDetails>;
    pagination: ElectionsPagination;
  };
}

export interface ElectionDetailBootstrap {
  infoApiUrl: string;
  candidatesApiUrl: string;
  userProfileUrlTemplate: string;
}

export interface ElectionExtendActionBootstrap {
  extendApiUrl: string;
  electionName: string;
  currentEndDateTimeValue: string;
  currentEndDateTimeDisplay: string;
}

export interface ElectionConcludeActionBootstrap {
  concludeApiUrl: string;
  electionName: string;
  quorumWarning: string;
}

export interface ElectionTallyActionBootstrap {
  tallyApiUrl: string;
  electionName: string;
}

export interface ElectionCredentialResendBootstrap {
  sendMailCredentialsApiUrl: string;
  eligibleUsernames: string[];
}

export interface ElectionActionCardBootstrap {
  infoApiUrl: string;
  voteUrl: string;
  membershipRequestUrl: string;
  auditLogUrl: string;
  publicBallotsUrl: string;
  publicAuditUrl: string;
}

export interface IneligibleVoterModalBootstrap {
  cardId: string;
  detailsJsonId: string;
}

export interface EligibleVotersBootstrap {
  eligibleVotersApiUrl: string;
  ineligibleVotersApiUrl: string;
  sendMailCredentialsApiUrl: string | null;
}

export interface ElectionVoterSearchBootstrap {
  fieldName: string;
  value: string;
  placeholder: string;
  ariaLabel: string;
  submitTitle: string;
  width: string;
}

export interface ElectionDetailRouteState {
  pathname: string;
  candidatePage: number;
}

export function readElectionDetailBootstrap(root: HTMLElement): ElectionDetailBootstrap | null {
  const infoApiUrl = String(root.dataset.electionDetailInfoApiUrl || "").trim();
  const candidatesApiUrl = String(root.dataset.electionDetailCandidatesApiUrl || "").trim();
  const userProfileUrlTemplate = String(root.dataset.electionDetailUserProfileUrlTemplate || "").trim();
  if (!infoApiUrl || !candidatesApiUrl || !userProfileUrlTemplate) {
    return null;
  }
  return { infoApiUrl, candidatesApiUrl, userProfileUrlTemplate };
}

export function readElectionExtendActionBootstrap(root: HTMLElement): ElectionExtendActionBootstrap | null {
  const extendApiUrl = String(root.dataset.electionExtendApiUrl || "").trim();
  const electionName = String(root.dataset.electionName || "").trim();
  const currentEndDateTimeValue = String(root.dataset.electionCurrentEndDatetimeValue || "").trim();
  const currentEndDateTimeDisplay = String(root.dataset.electionCurrentEndDatetimeDisplay || "").trim();
  if (!extendApiUrl || !electionName || !currentEndDateTimeValue || !currentEndDateTimeDisplay) {
    return null;
  }
  return {
    extendApiUrl,
    electionName,
    currentEndDateTimeValue,
    currentEndDateTimeDisplay,
  };
}

export function readElectionConcludeActionBootstrap(root: HTMLElement): ElectionConcludeActionBootstrap | null {
  const concludeApiUrl = String(root.dataset.electionConcludeApiUrl || "").trim();
  const electionName = String(root.dataset.electionName || "").trim();
  const quorumWarning = String(root.dataset.electionConcludeQuorumWarning || "").trim();
  if (!concludeApiUrl || !electionName) {
    return null;
  }
  return {
    concludeApiUrl,
    electionName,
    quorumWarning,
  };
}

export function readElectionTallyActionBootstrap(root: HTMLElement): ElectionTallyActionBootstrap | null {
  const tallyApiUrl = String(root.dataset.electionTallyApiUrl || "").trim();
  const electionName = String(root.dataset.electionName || "").trim();
  if (!tallyApiUrl || !electionName) {
    return null;
  }
  return {
    tallyApiUrl,
    electionName,
  };
}

export function readElectionCredentialResendBootstrap(root: HTMLElement): ElectionCredentialResendBootstrap | null {
  const sendMailCredentialsApiUrl = String(root.dataset.electionSendMailCredentialsApiUrl || "").trim();
  if (!sendMailCredentialsApiUrl) {
    return null;
  }

  const jsonElement = root.querySelector<HTMLScriptElement>("#election-eligible-voter-usernames-json");
  let eligibleUsernames: string[] = [];
  if (jsonElement?.textContent) {
    try {
      const raw = JSON.parse(jsonElement.textContent) as unknown;
      if (Array.isArray(raw)) {
        eligibleUsernames = raw.filter((value): value is string => typeof value === "string");
      }
    } catch {
      eligibleUsernames = [];
    }
  }

  return {
    sendMailCredentialsApiUrl,
    eligibleUsernames,
  };
}

export function readElectionActionCardBootstrap(root: HTMLElement): ElectionActionCardBootstrap | null {
  const infoApiUrl = String(root.dataset.electionDetailInfoApiUrl || "").trim();
  const voteUrl = String(root.dataset.electionVoteUrl || "").trim();
  const membershipRequestUrl = String(root.dataset.electionMembershipRequestUrl || "").trim();
  const auditLogUrl = String(root.dataset.electionAuditLogUrl || "").trim();
  const publicBallotsUrl = String(root.dataset.electionPublicBallotsUrl || "").trim();
  const publicAuditUrl = String(root.dataset.electionPublicAuditUrl || "").trim();
  if (!infoApiUrl || !voteUrl || !membershipRequestUrl || !auditLogUrl || !publicBallotsUrl || !publicAuditUrl) {
    return null;
  }
  return {
    infoApiUrl,
    voteUrl,
    membershipRequestUrl,
    auditLogUrl,
    publicBallotsUrl,
    publicAuditUrl,
  };
}

export function readIneligibleVoterModalBootstrap(root: HTMLElement): IneligibleVoterModalBootstrap | null {
  const cardId = String(root.dataset.ineligibleVoterCardId || "").trim();
  const detailsJsonId = String(root.dataset.ineligibleVoterDetailsJsonId || "").trim();
  if (!cardId || !detailsJsonId) {
    return null;
  }
  return {
    cardId,
    detailsJsonId,
  };
}

export function readEligibleVotersBootstrap(root: HTMLElement): EligibleVotersBootstrap | null {
  const eligibleVotersApiUrl = String(root.dataset.electionEligibleVotersApiUrl || "").trim();
  const ineligibleVotersApiUrl = String(root.dataset.electionIneligibleVotersApiUrl || "").trim();
  if (!eligibleVotersApiUrl || !ineligibleVotersApiUrl) {
    return null;
  }
  const sendMailCredentialsApiUrl = String(root.dataset.electionSendMailCredentialsApiUrl || "").trim() || null;
  return { eligibleVotersApiUrl, ineligibleVotersApiUrl, sendMailCredentialsApiUrl };
}

export function readElectionVoterSearchBootstrap(root: HTMLElement): ElectionVoterSearchBootstrap | null {
  const fieldName = String(root.dataset.electionSearchFieldName || "").trim();
  const value = String(root.dataset.electionSearchValue || "");
  const placeholder = String(root.dataset.electionSearchPlaceholder || "").trim();
  const ariaLabel = String(root.dataset.electionSearchAriaLabel || "").trim();
  const submitTitle = String(root.dataset.electionSearchSubmitTitle || "").trim();
  const width = String(root.dataset.electionSearchWidth || "220px").trim();
  if (!fieldName || !placeholder || !ariaLabel || !submitTitle || !width) {
    return null;
  }
  return {
    fieldName,
    value,
    placeholder,
    ariaLabel,
    submitTitle,
    width,
  };
}

export function readElectionDetailRouteState(currentUrl: string): ElectionDetailRouteState {
  const url = new URL(currentUrl, "https://example.test");
  const candidatePage = Number.parseInt(url.searchParams.get("candidate_page") || "1", 10);
  return {
    pathname: url.pathname,
    candidatePage: Number.isNaN(candidatePage) || candidatePage < 1 ? 1 : candidatePage,
  };
}

export function buildElectionDetailRouteUrl(state: ElectionDetailRouteState, currentUrl?: string): string {
  const url = new URL(currentUrl || state.pathname, "https://example.test");
  if (state.candidatePage > 1) {
    url.searchParams.set("candidate_page", String(state.candidatePage));
  } else {
    url.searchParams.delete("candidate_page");
  }
  return `${url.pathname}${url.search}`;
}