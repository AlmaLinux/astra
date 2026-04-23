import { ref, type Ref } from "vue";

import type {
  MembershipStatsBootstrap,
  SummaryData,
  CompositionChartsData,
  TrendsChartsData,
  RetentionChartsData,
  DaysPreset,
} from "../types";
import { isDaysPreset } from "../types";

export interface MembershipStatsState {
  summary: Ref<SummaryData | null>;
  compositionCharts: Ref<CompositionChartsData | null>;
  trendsCharts: Ref<TrendsChartsData | null>;
  retentionCharts: Ref<RetentionChartsData | null>;
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
  const currentDays = ref<DaysPreset>(isDaysPreset(bootstrap.currentDays) ? bootstrap.currentDays : "365");
  const isLoading = ref(false);
  const error = ref<string | null>(null);

  async function load(days: DaysPreset): Promise<void> {
    isLoading.value = true;
    error.value = null;
    currentDays.value = days;

    const daysQuery = `?days=${days}`;

    try {
      const [summaryResp, compositionResp, trendsResp, retentionResp] = await Promise.all([
        fetch(`${bootstrap.apiSummaryUrl}${daysQuery}`),
        fetch(bootstrap.apiCompositionUrl),
        fetch(`${bootstrap.apiTrendsUrl}${daysQuery}`),
        fetch(bootstrap.apiRetentionUrl),
      ]);

      if (!summaryResp.ok || !compositionResp.ok || !trendsResp.ok || !retentionResp.ok) {
        error.value = "Failed to load statistics. Please try again.";
        return;
      }

      const [summaryData, compositionData, trendsData, retentionData] = await Promise.all([
        summaryResp.json() as Promise<{ summary: SummaryData }>,
        compositionResp.json() as Promise<{ charts: CompositionChartsData }>,
        trendsResp.json() as Promise<{ charts: TrendsChartsData }>,
        retentionResp.json() as Promise<{ charts: RetentionChartsData }>,
      ]);

      summary.value = summaryData.summary;
      compositionCharts.value = compositionData.charts;
      trendsCharts.value = trendsData.charts;
      retentionCharts.value = retentionData.charts;
    } catch {
      error.value = "Network error loading statistics.";
    } finally {
      isLoading.value = false;
    }
  }

  return { summary, compositionCharts, trendsCharts, retentionCharts, currentDays, isLoading, error, load };
}
