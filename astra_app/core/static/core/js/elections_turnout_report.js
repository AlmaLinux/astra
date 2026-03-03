(function () {
  var chartElement = document.getElementById("elections-turnout-report-chart");
  var payloadElement = document.getElementById("elections-turnout-report-chart-data");

  if (!chartElement || !payloadElement || typeof window.Chart === "undefined") {
    return;
  }

  var payload = {};
  try {
    payload = JSON.parse(payloadElement.textContent || "{}");
  } catch (error) {
    return;
  }

  var labels = Array.isArray(payload.labels) ? payload.labels : [];
  var countTurnout = Array.isArray(payload.count_turnout) ? payload.count_turnout : [];
  var weightTurnout = Array.isArray(payload.weight_turnout) ? payload.weight_turnout : [];

  if (!labels.length) {
    return;
  }

  var ctx = chartElement.getContext("2d");
  if (!ctx) {
    return;
  }

  new window.Chart(ctx, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Turnout % (count)",
          data: countTurnout,
          backgroundColor: "rgba(54, 162, 235, 0.7)",
          borderColor: "rgba(54, 162, 235, 1)",
          borderWidth: 1,
        },
        {
          label: "Turnout % (weight)",
          data: weightTurnout,
          backgroundColor: "rgba(255, 159, 64, 0.7)",
          borderColor: "rgba(255, 159, 64, 1)",
          borderWidth: 1,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          beginAtZero: true,
          max: 100,
          ticks: {
            callback: function (value) {
              return value + "%";
            },
          },
        },
      },
      plugins: {
        tooltip: {
          callbacks: {
            label: function (context) {
              return context.dataset.label + ": " + context.parsed.y + "%";
            },
          },
        },
      },
    },
  });
})();
