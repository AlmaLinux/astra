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

  function get2dContext(canvasId) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return null;
    return canvas.getContext('2d');
  }

  function renderDoughnut(canvasId, labels, data, title) {
    var ctx = get2dContext(canvasId);
    if (!ctx || !window.Chart) return;
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
      options: {
        responsive: true,
        maintainAspectRatio: false,
      },
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
        'Nationality (all users)'
      );
    }, 'nationality-all-users');

    var natMem = charts.nationality_active_members || { labels: [], counts: [] };
    renderChartSafely(function () {
      renderDoughnut(
        'nationality-active-members-chart',
        natMem.labels || [],
        natMem.counts || [],
        'Nationality (active members)'
      );
    }, 'nationality-active-members');
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
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
