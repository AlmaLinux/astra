export const DAYS_PRESETS = ["30", "90", "180", "365", "all"] as const;
export type DaysPreset = (typeof DAYS_PRESETS)[number];

export function isDaysPreset(value: string): value is DaysPreset {
  return (DAYS_PRESETS as readonly string[]).includes(value);
}

export interface MembershipStatsBootstrap {
  currentDays: DaysPreset;
  apiSummaryUrl: string;
  apiCompositionUrl: string;
  apiTrendsUrl: string;
  apiRetentionUrl: string;
}

export function readMembershipStatsBootstrap(root: HTMLElement): MembershipStatsBootstrap | null {
  const {
    membershipStatsCurrentDays,
    membershipStatsApiSummaryUrl,
    membershipStatsApiCompositionUrl,
    membershipStatsApiTrendsUrl,
    membershipStatsApiRetentionUrl,
  } = root.dataset;

  if (
    !membershipStatsApiSummaryUrl ||
    !membershipStatsApiCompositionUrl ||
    !membershipStatsApiTrendsUrl ||
    !membershipStatsApiRetentionUrl
  ) {
    return null;
  }

  const days = isDaysPreset(membershipStatsCurrentDays ?? "") ? (membershipStatsCurrentDays as DaysPreset) : "365";

  return {
    currentDays: days,
    apiSummaryUrl: membershipStatsApiSummaryUrl,
    apiCompositionUrl: membershipStatsApiCompositionUrl,
    apiTrendsUrl: membershipStatsApiTrendsUrl,
    apiRetentionUrl: membershipStatsApiRetentionUrl,
  };
}

// --- API response shapes ---

export interface ApprovalTimeStats {
  mean_hours: number | null;
  median_hours: number | null;
  p90_hours: number | null;
  sample_size: number;
  outlier_cutoff_days: number;
}

export interface RetentionCohortSummary {
  cohorts: number;
  users: number;
}

export interface SummaryData {
  total_freeipa_users: number;
  active_individual_memberships: number;
  active_org_sponsorships: number;
  pending_requests: number;
  on_hold_requests: number;
  expiring_soon_90_days: number;
  approval_time: ApprovalTimeStats;
  retention_cohort_12m: RetentionCohortSummary;
}

export interface ChartDistribution {
  labels: string[];
  counts: number[];
}

export interface DecisionDataset {
  label: string;
  data: number[];
}

export interface TrendBarChart {
  labels: string[];
  counts: number[];
}

export interface TrendDecisionsChart {
  labels: string[];
  datasets: DecisionDataset[];
}

export interface RetentionCohortChart {
  labels: string[];
  retained: number[];
  lapsed_then_renewed: number[];
  lapsed_not_renewed: number[];
}

export interface CompositionChartsData {
  membership_types: ChartDistribution;
  nationality_all_users: ChartDistribution;
  nationality_active_members: ChartDistribution;
}

export interface TrendsChartsData {
  requests_trend: TrendBarChart;
  decisions_trend: TrendDecisionsChart;
  expirations_upcoming: TrendBarChart;
}

export interface RetentionChartsData {
  retention_cohorts_12m: RetentionCohortChart;
}
