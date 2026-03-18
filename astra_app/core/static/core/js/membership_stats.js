(function () {
  function hideLoading(key) {
    var el = document.querySelector('[data-chart-loading="' + key + '"]');
    if (!el) return;
    // Bootstrap/AdminLTE uses utilities like `d-flex` with `display: flex !important`.
    // A plain `el.style.display = 'none'` can be overridden; force-hide with inline !important.
    el.style.setProperty('display', 'none', 'important');
    el.classList.remove('d-flex');
    el.setAttribute('aria-hidden', 'true');
  }

  function renderChartSafely(renderFn, loadingKey) {
    try {
      renderFn();
    } catch (err) {
      console.warn('[membership-stats] chart render failed:', loadingKey, err);
    } finally {
      hideLoading(loadingKey);
    }
  }

  function setSummaryValue(key, value) {
    var el = document.querySelector('[data-stat-key="' + key + '"]');
    if (!el) return;
    el.textContent = String(value);
  }

  function formatMetricValue(value) {
    if (value === null || value === undefined || value === '') return 'N/A';
    return String(value);
  }

  function formatHoursDuration(hours) {
    if (hours === null || hours === undefined) return 'N/A';

    var numericHours = Number(hours);
    if (!Number.isFinite(numericHours) || Number.isNaN(numericHours)) return 'N/A';

    if (numericHours < 24) {
      var totalMinutes = Math.round(numericHours * 60);
      if (totalMinutes >= 24 * 60) {
        return (numericHours / 24).toFixed(1) + ' days';
      }

      var wholeHours = Math.floor(totalMinutes / 60);
      var remainingMinutes = totalMinutes % 60;
      return wholeHours + 'h ' + remainingMinutes + 'm';
    }

    if (numericHours < 168) {
      return (numericHours / 24).toFixed(1) + ' days';
    }

    return (numericHours / 168).toFixed(1) + ' weeks';
  }

  function get2dContext(canvasId) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return null;
    return canvas.getContext('2d');
  }

  function visibleTotal(chart, datasetIndex) {
    if (!chart || !chart.data || !chart.data.datasets) return 0;
    var dataset = chart.data.datasets[datasetIndex] || { data: [] };
    var values = Array.isArray(dataset.data) ? dataset.data : [];

    var meta = null;
    if (typeof chart.getDatasetMeta === 'function') {
      meta = chart.getDatasetMeta(datasetIndex);
    }

    var total = 0;
    for (var i = 0; i < values.length; i++) {
      var isVisible = true;
      if (typeof chart.getDataVisibility === 'function') {
        isVisible = chart.getDataVisibility(i);
      } else if (meta && meta.data && meta.data[i]) {
        // Best-effort fallback if getDataVisibility isn't available.
        isVisible = !meta.data[i].hidden;
      }

      if (!isVisible) continue;

      var numeric = Number(values[i]);
      if (Number.isFinite(numeric)) total += numeric;
    }

    return total;
  }

  function renderDoughnut(canvasId, labels, data, title, opts) {
    var ctx = get2dContext(canvasId);
    if (!ctx || !window.Chart) return;
    var options = opts || {};
    var dynamicPercentTooltip = options.dynamicPercentTooltip !== false;

    var chartOptions = {
      responsive: true,
      maintainAspectRatio: false,
    };

    if (dynamicPercentTooltip) {
      chartOptions.plugins = {
        tooltip: {
          callbacks: {
            title: function (items) {
              if (!items || !items.length) return '';
              return String(items[0].label || '');
            },
            label: function (context) {
              var value = Number(context.parsed);
              if (!Number.isFinite(value)) value = 0;

              var total = visibleTotal(context.chart, context.datasetIndex);
              if (total <= 0) {
                return String(value);
              }

              var pct = (value / total) * 100;
              return String(value) + ' (' + pct.toFixed(1) + '%)';
            },
          },
        },
      };
    }
    new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: labels,
        datasets: [
          {
            label: title,
            data: data,
          },
        ],
      },
      options: chartOptions,
    });
  }

  function renderLine(canvasId, labels, data, title) {
    var ctx = get2dContext(canvasId);
    if (!ctx || !window.Chart) return;
    new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [
          {
            label: title,
            data: data,
            borderColor: 'rgba(60,141,188,0.8)',
            backgroundColor: 'rgba(60,141,188,0.2)',
            fill: true,
            tension: 0.2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: {
            beginAtZero: true,
            ticks: { precision: 0 },
          },
        },
      },
    });
  }

  function renderStackedBar(canvasId, labels, datasets) {
    var ctx = get2dContext(canvasId);
    if (!ctx || !window.Chart) return;
    new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: datasets,
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { stacked: true },
          y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } },
        },
      },
    });
  }

  function renderBar(canvasId, labels, data, title) {
    var ctx = get2dContext(canvasId);
    if (!ctx || !window.Chart) return;
    new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [
          {
            label: title,
            data: data,
            backgroundColor: 'rgba(60,141,188,0.7)',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: { beginAtZero: true, ticks: { precision: 0 } },
        },
      },
    });
  }

  function render(payload) {
    var summary = payload && payload.summary ? payload.summary : {};
    setSummaryValue('total_freeipa_users', summary.total_freeipa_users || 0);
    setSummaryValue('active_individual_memberships', summary.active_individual_memberships || 0);
    setSummaryValue('pending_requests', summary.pending_requests || 0);
    setSummaryValue('on_hold_requests', summary.on_hold_requests || 0);
    setSummaryValue('expiring_soon_90_days', summary.expiring_soon_90_days || 0);

    var approval = summary.approval_time || {};
    setSummaryValue('approval_time_mean_hours', formatHoursDuration(approval.mean_hours));
    setSummaryValue('approval_time_median_hours', formatHoursDuration(approval.median_hours));
    setSummaryValue('approval_time_p90_hours', formatHoursDuration(approval.p90_hours));

    var retentionSummary = summary.retention_cohort_12m || {};
    setSummaryValue('retention_cohorts_count', formatMetricValue(retentionSummary.cohorts || 0));
    setSummaryValue('retention_retained_count', formatMetricValue(retentionSummary.retained || 0));
    setSummaryValue(
      'retention_lapsed_then_renewed_count',
      formatMetricValue(retentionSummary.lapsed_then_renewed || 0)
    );
    setSummaryValue(
      'retention_lapsed_not_renewed_count',
      formatMetricValue(retentionSummary.lapsed_not_renewed || 0)
    );

    var charts = payload && payload.charts ? payload.charts : {};

    var types = charts.membership_types || { labels: [], counts: [] };
    renderChartSafely(function () {
      renderDoughnut('membership-types-chart', types.labels || [], types.counts || [], 'Membership types');
    }, 'membership-types');

    var reqs = charts.requests_trend || { labels: [], counts: [] };
    renderChartSafely(function () {
      renderLine('requests-trend-chart', reqs.labels || [], reqs.counts || [], 'Requests');
    }, 'requests-trend');

    var decisions = charts.decisions_trend || { labels: [], datasets: [] };
    renderChartSafely(function () {
      renderStackedBar('decisions-trend-chart', decisions.labels || [], decisions.datasets || []);
    }, 'decisions-trend');

    var exp = charts.expirations_upcoming || { labels: [], counts: [] };
    renderChartSafely(function () {
      renderBar('expirations-upcoming-chart', exp.labels || [], exp.counts || [], 'Expirations');
    }, 'expirations-upcoming');

    var natAll = charts.nationality_all_users || { labels: [], counts: [] };
    renderChartSafely(function () {
      renderDoughnut(
        'nationality-all-users-chart',
        natAll.labels || [],
        natAll.counts || [],
        'Country code (all active FreeIPA users)'
      );
    }, 'nationality-all-users');

    var natMem = charts.nationality_active_members || { labels: [], counts: [] };
    renderChartSafely(function () {
      renderDoughnut(
        'nationality-active-members-chart',
        natMem.labels || [],
        natMem.counts || [],
        'Country code (active individual members)'
      );
    }, 'nationality-active-members');

    var retentionCohorts = charts.retention_cohorts_12m || {
      labels: [],
      retained: [],
      lapsed_then_renewed: [],
      lapsed_not_renewed: [],
    };
    renderChartSafely(function () {
      renderStackedBar('retention-cohorts-chart', retentionCohorts.labels || [], [
        {
          label: 'Retained',
          data: retentionCohorts.retained || [],
          backgroundColor: 'rgba(40,167,69,0.75)',
        },
        {
          label: 'Lapsed then renewed',
          data: retentionCohorts.lapsed_then_renewed || [],
          backgroundColor: 'rgba(255,193,7,0.8)',
        },
        {
          label: 'Lapsed (not renewed)',
          data: retentionCohorts.lapsed_not_renewed || [],
          backgroundColor: 'rgba(220,53,69,0.75)',
        },
      ]);
    }, 'retention-cohorts');
  }

  function init() {
    var root = document.getElementById('membership-stats-root');
    if (!root) return;

    var url = String(root.getAttribute('data-url') || '').trim();
    if (!url) return;

    fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (resp) {
        if (!resp.ok) throw new Error('Failed to load stats: ' + resp.status);
        return resp.json();
      })
      .then(function (payload) {
        render(payload);
      })
      .catch(function (err) {
        console.warn('[membership-stats] failed:', err);
        // Hide spinners to avoid indefinite loading.
        hideLoading('membership-types');
        hideLoading('requests-trend');
        hideLoading('decisions-trend');
        hideLoading('expirations-upcoming');
        hideLoading('nationality-all-users');
        hideLoading('nationality-active-members');
        hideLoading('retention-cohorts');
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
