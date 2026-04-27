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

export interface MembershipTypeDistributionRow {
  membership_type: {
    code: string;
    name: string;
  };
  count: number;
}

export interface CountryDistributionRow {
  country_code: string;
  count: number;
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

export interface PeriodCountRow {
  period: string;
  count: number;
}

export interface DecisionTrendRow {
  period: string;
  status: string;
  count: number;
}

export interface RetentionChartsData {
  retention_cohorts_12m: RetentionCohortChart;
}

export interface RetentionCohortRow {
  cohort_month: string;
  cohort_size: number;
  retained: number;
  lapsed_then_renewed: number;
  lapsed_not_renewed: number;
}

export interface CompositionChartsApiData {
  membership_types: MembershipTypeDistributionRow[];
  nationality_all_users: CountryDistributionRow[];
  nationality_active_members: CountryDistributionRow[];
}

export interface TrendsChartsApiData {
  requests_trend: PeriodCountRow[];
  decisions_trend: DecisionTrendRow[];
  expirations_upcoming: PeriodCountRow[];
}

const DECISION_TREND_STATUSES = ["approved", "rejected", "ignored", "rescinded"] as const;

export interface RetentionChartsApiData {
  retention_cohorts_12m: RetentionCohortRow[];
}

export function compositionChartsFromApi(charts: CompositionChartsApiData): CompositionChartsData {
  return {
    membership_types: {
      labels: charts.membership_types.map((row) => row.membership_type.name),
      counts: charts.membership_types.map((row) => row.count),
    },
    nationality_all_users: {
      labels: charts.nationality_all_users.map((row) => row.country_code),
      counts: charts.nationality_all_users.map((row) => row.count),
    },
    nationality_active_members: {
      labels: charts.nationality_active_members.map((row) => row.country_code),
      counts: charts.nationality_active_members.map((row) => row.count),
    },
  };
}

export function trendsChartsFromApi(charts: TrendsChartsApiData): TrendsChartsData {
  const decisionPeriods = Array.from(new Set(charts.decisions_trend.map((row) => row.period))).sort();
  const decisionIndex = new Map(charts.decisions_trend.map((row) => [`${row.period}:${row.status}`, row.count]));
  return {
    requests_trend: {
      labels: charts.requests_trend.map((row) => row.period),
      counts: charts.requests_trend.map((row) => row.count),
    },
    decisions_trend: {
      labels: decisionPeriods,
      datasets: DECISION_TREND_STATUSES.map((status) => ({
        label: status,
        data: decisionPeriods.map((period) => decisionIndex.get(`${period}:${status}`) || 0),
      })),
    },
    expirations_upcoming: {
      labels: charts.expirations_upcoming.map((row) => row.period),
      counts: charts.expirations_upcoming.map((row) => row.count),
    },
  };
}

export function retentionChartsFromApi(charts: RetentionChartsApiData): RetentionChartsData {
  return {
    retention_cohorts_12m: {
      labels: charts.retention_cohorts_12m.map((row) => row.cohort_month),
      retained: charts.retention_cohorts_12m.map((row) => row.retained),
      lapsed_then_renewed: charts.retention_cohorts_12m.map((row) => row.lapsed_then_renewed),
      lapsed_not_renewed: charts.retention_cohorts_12m.map((row) => row.lapsed_not_renewed),
    },
  };
}
