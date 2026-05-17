import { ref, type Ref } from "vue";

import type {
  MembershipStatsBootstrap,
  SummaryData,
  CompositionChartsData,
  CompositionChartsApiData,
  TrendsChartsData,
  TrendsChartsApiData,
  TrendsChartPeriodBuckets,
  RetentionChartsData,
  RetentionChartsApiData,
  ActiveMembershipsChartsData,
  ActiveMembershipsChartsApiData,
  DaysPreset,
  PeriodBucket,
} from "../types";
import {
  activeMembershipsChartsFromApi,
  compositionChartsFromApi,
  isDaysPreset,
  retentionChartsFromApi,
  trendsChartsFromApi,
} from "../types";

export interface MembershipStatsState {
  summary: Ref<SummaryData | null>;
  compositionCharts: Ref<CompositionChartsData | null>;
  trendsCharts: Ref<TrendsChartsData | null>;
  retentionCharts: Ref<RetentionChartsData | null>;
  activeMembershipsCharts: Ref<ActiveMembershipsChartsData | null>;
  currentDays: Ref<DaysPreset>;
  isLoading: Ref<boolean>;
  error: Ref<string | null>;
  load: (days: DaysPreset) => Promise<void>;
}

export function useMembershipStats(bootstrap: MembershipStatsBootstrap): MembershipStatsState {
  const summary = ref<SummaryData | null>(null);
  const compositionCharts = ref<CompositionChartsData | null>(null);
  const trendsCharts = ref<TrendsChartsData | null>(null);
  const retentionCharts = ref<RetentionChartsData | null>(null);
  const activeMembershipsCharts = ref<ActiveMembershipsChartsData | null>(null);
  const currentDays = ref<DaysPreset>(isDaysPreset(bootstrap.currentDays) ? bootstrap.currentDays : "365");
  const isLoading = ref(false);
  const error = ref<string | null>(null);

  async function load(days: DaysPreset): Promise<void> {
    isLoading.value = true;
    error.value = null;
    currentDays.value = days;

    const daysQuery = `?days=${days}`;

    try {
      const [summaryResp, compositionResp, trendsResp, retentionResp, activeMembershipsResp] = await Promise.all([
        fetch(`${bootstrap.apiSummaryUrl}${daysQuery}`),
        fetch(bootstrap.apiCompositionUrl),
        fetch(`${bootstrap.apiTrendsUrl}${daysQuery}`),
        fetch(bootstrap.apiRetentionUrl),
        fetch(`${bootstrap.apiActiveMembershipsUrl}${daysQuery}`),
      ]);

      if (!summaryResp.ok || !compositionResp.ok || !trendsResp.ok || !retentionResp.ok || !activeMembershipsResp.ok) {
        error.value = "Failed to load statistics. Please try again.";
        return;
      }

      const [summaryData, compositionData, trendsData, retentionData, activeMembershipsData] = await Promise.all([
        summaryResp.json() as Promise<{ summary: SummaryData }>,
        compositionResp.json() as Promise<{ charts: CompositionChartsApiData }>,
        trendsResp.json() as Promise<{
          charts: TrendsChartsApiData;
          period_bucket: PeriodBucket;
          chart_period_buckets: TrendsChartPeriodBuckets;
        }>,
        retentionResp.json() as Promise<{ charts: RetentionChartsApiData; period_bucket: PeriodBucket }>,
        activeMembershipsResp.json() as Promise<{ charts: ActiveMembershipsChartsApiData; period_bucket: PeriodBucket }>,
      ]);

      summary.value = summaryData.summary;
      compositionCharts.value = compositionChartsFromApi(compositionData.charts);
      trendsCharts.value = trendsChartsFromApi(
        trendsData.charts,
        trendsData.period_bucket,
        trendsData.chart_period_buckets,
      );
      retentionCharts.value = retentionChartsFromApi(retentionData.charts, retentionData.period_bucket);
      activeMembershipsCharts.value = activeMembershipsChartsFromApi(
        activeMembershipsData.charts,
        activeMembershipsData.period_bucket,
      );
    } catch {
      error.value = "Network error loading statistics.";
    } finally {
      isLoading.value = false;
    }
  }

  return {
    summary,
    compositionCharts,
    trendsCharts,
    retentionCharts,
    activeMembershipsCharts,
    currentDays,
    isLoading,
    error,
    load,
  };
}
