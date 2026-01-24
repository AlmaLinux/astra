(function () {
  function parseJson(dataEl) {
    if (!dataEl) return [];
    try {
      return JSON.parse(dataEl.textContent || '[]');
    } catch (e) {
      return [];
    }
  }

  function roundIndex(label) {
    var match = /^Round\s+(\d+)/.exec(label);
    if (!match) return 0;
    return parseInt(match[1], 10) || 0;
  }

  function splitNode(nodeId) {
    var raw = String(nodeId || '');
    if (raw.indexOf('Round ') === 0) {
      var parts = raw.split(' · ');
      if (parts.length >= 2) {
        return {
          roundName: parts[0],
          candidate: parts.slice(1).join(' · '),
          isRoundNode: true
        };
      }
      return {
        roundName: raw,
        candidate: raw,
        isRoundNode: true
      };
    }
    return {
      roundName: '',
      candidate: raw,
      isRoundNode: false
    };
  }

  function buildChart(canvas, flows, electedNodes, eliminatedNodes) {
    if (!canvas || !canvas.getContext || !window.Chart) return;

    var roundNames = {};
    var candidateNames = {};
    var nodeIds = {};

    flows.forEach(function (flow) {
      var from = String(flow.from || '');
      var to = String(flow.to || '');
      var fromInfo = splitNode(from);
      var toInfo = splitNode(to);

      nodeIds[from] = true;
      nodeIds[to] = true;

      if (fromInfo.roundName) {
        roundNames[fromInfo.roundName] = true;
      }
      if (toInfo.roundName) {
        roundNames[toInfo.roundName] = true;
      }

      if (fromInfo.candidate && fromInfo.candidate !== 'Voters') {
        candidateNames[fromInfo.candidate] = true;
      }
      if (toInfo.candidate && toInfo.candidate !== 'Voters') {
        candidateNames[toInfo.candidate] = true;
      }
    });

    var rounds = Object.keys(roundNames).sort(function (a, b) {
      return roundIndex(a) - roundIndex(b);
    });
    var candidates = Object.keys(candidateNames).sort();

    var priority = {};
    priority.Voters = 0;
    Object.keys(nodeIds).forEach(function (nodeId) {
      if (nodeId === 'Voters') {
        return;
      }
      var info = splitNode(nodeId);
      if (info.roundName) {
        priority[nodeId] = roundIndex(info.roundName);
      }
    });

    var palette = [
      '#1f77b4',
      '#ff7f0e',
      '#2ca02c',
      '#d62728',
      '#9467bd',
      '#8c564b',
      '#e377c2',
      '#7f7f7f',
      '#bcbd22',
      '#17becf'
    ];
    var colors = {};
    candidates.forEach(function (label, idx) {
      colors[label] = palette[idx % palette.length];
    });

    function getColor(name) {
      if (name === 'Voters') {
        return '#082336';
      }
      return colors[name] || '#6c757d';
    }

    var labels = {};
    var electedSet = {};
    if (Array.isArray(electedNodes)) {
      electedNodes.forEach(function (nodeId) {
        var name = String(nodeId || '').trim();
        if (name) {
          electedSet[name] = true;
        }
      });
    }

    var eliminatedSet = {};
    if (Array.isArray(eliminatedNodes)) {
      eliminatedNodes.forEach(function (nodeId) {
        var name = String(nodeId || '').trim();
        if (name) {
          eliminatedSet[name] = true;
        }
      });
    }

    var candidateRounds = {};
    var firstElectedRound = {};
    Object.keys(nodeIds).forEach(function (nodeId) {
      if (nodeId === 'Voters') {
        return;
      }
      var info = splitNode(nodeId);
      if (!info.roundName) {
        return;
      }
      var idx = roundIndex(info.roundName);
      if (!idx) {
        return;
      }
      var ranges = candidateRounds[info.candidate] || { min: idx, max: idx };
      if (idx < ranges.min) {
        ranges.min = idx;
      }
      if (idx > ranges.max) {
        ranges.max = idx;
      }
      candidateRounds[info.candidate] = ranges;
    });

    if (Array.isArray(electedNodes)) {
      electedNodes.forEach(function (nodeId) {
        var info = splitNode(nodeId);
        if (!info.roundName) {
          return;
        }
        var roundIdx = roundIndex(info.roundName);
        if (!roundIdx) {
          return;
        }
        if (!firstElectedRound[info.candidate] || roundIdx < firstElectedRound[info.candidate]) {
          firstElectedRound[info.candidate] = roundIdx;
        }
      });
    }

    Object.keys(nodeIds).forEach(function (nodeId) {
      if (nodeId === 'Voters') {
        labels[nodeId] = 'AlmaLinux\nCommunity\nVoters';
        return;
      }
      var info = splitNode(nodeId);
      var candidateLabel = info.candidate || nodeId;
      if (info.roundName) {
        var roundIdx = roundIndex(info.roundName);
        var ranges = candidateRounds[candidateLabel];
        var electedIdx = firstElectedRound[candidateLabel] || null;
        if (ranges && roundIdx !== ranges.min && roundIdx !== ranges.max && roundIdx !== electedIdx) {
          labels[nodeId] = '';
          return;
        }
      }
      var prefix = '';
      if (eliminatedSet[nodeId]) {
        prefix = '❌ ';
      } else if (electedSet[nodeId]) {
        prefix = '✅ ';
      }
      labels[nodeId] = prefix + candidateLabel;
    });

    var ctx = canvas.getContext('2d');
    var chart = new Chart(ctx, {
      type: 'sankey',
      data: {
        datasets: [
          {
            data: flows,
            priority: priority,
            labels: labels,
            colorFrom: function (context) {
              var row = context.dataset.data[context.dataIndex] || {};
              var info = splitNode(row.from);
              return getColor(info.candidate);
            },
            colorTo: function (context) {
              var row = context.dataset.data[context.dataIndex] || {};
              var info = splitNode(row.to);
              return getColor(info.candidate);
            },
            colorMode: 'gradient',
            borderWidth: 1,
            borderColor: '#111827'
          }
        ]
      },
      options: {
        plugins: {
          legend: {
            display: false
          }
        }
      }
    });

    attachNodeTooltip(chart, labels);
  }

  function attachNodeTooltip(chart, labels) {
    if (!chart || !chart.canvas) return;

    var canvas = chart.canvas;
    var parent = canvas.parentNode;
    if (!parent) return;

    if (window.getComputedStyle(parent).position === 'static') {
      parent.style.position = 'relative';
    }

    var tooltipEl = document.createElement('div');
    tooltipEl.style.position = 'absolute';
    tooltipEl.style.pointerEvents = 'none';
    tooltipEl.style.background = 'rgba(17, 24, 39, 0.9)';
    tooltipEl.style.color = '#f9fafb';
    tooltipEl.style.padding = '6px 8px';
    tooltipEl.style.borderRadius = '6px';
    tooltipEl.style.fontSize = '12px';
    tooltipEl.style.lineHeight = '1.2';
    tooltipEl.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.25)';
    tooltipEl.style.opacity = '0';
    tooltipEl.style.transition = 'opacity 120ms ease';
    parent.appendChild(tooltipEl);

    function nodeSize(node) {
      var incoming = typeof node.in === 'number' ? node.in : 0;
      var outgoing = typeof node.out === 'number' ? node.out : 0;
      return Math.max(incoming || outgoing, outgoing || incoming, 0);
    }

    function formatVotes(value) {
      if (!isFinite(value)) {
        return '';
      }
      var rounded = Math.round(value);
      if (Math.abs(value - rounded) < 1e-6) {
        return String(rounded);
      }
      return Number(value).toFixed(4);
    }

    function findNodeAt(x, y) {
      var meta = chart.getDatasetMeta(0);
      if (!meta || !meta.controller || !meta.controller._nodes) return null;
      var nodes = Array.from(meta.controller._nodes.values());
      var xScale = chart.scales.x;
      var yScale = chart.scales.y;
      if (!xScale || !yScale) return null;

      var nodeWidth = meta.controller.options.nodeWidth || 10;

      for (var i = 0; i < nodes.length; i += 1) {
        var node = nodes[i];
        var size = nodeSize(node);
        var xStart = xScale.getPixelForValue(node.x);
        var yStart = yScale.getPixelForValue(node.y);
        var height = Math.abs(yScale.getPixelForValue(node.y + size) - yStart);
        if (x >= xStart && x <= xStart + nodeWidth && y >= yStart && y <= yStart + height) {
          return node;
        }
      }

      return null;
    }

    function handleMove(evt) {
      var rect = canvas.getBoundingClientRect();
      var x = evt.clientX - rect.left;
      var y = evt.clientY - rect.top;
      var node = findNodeAt(x, y);
      if (!node) {
        tooltipEl.style.opacity = '0';
        return;
      }

      var info = splitNode(node.key);
      var roundLabel = info.roundName ? info.roundName + ' · ' : '';
      var candidateLabel = labels[node.key] || info.candidate || node.key;
      var votes = formatVotes(nodeSize(node));
      tooltipEl.textContent = roundLabel + candidateLabel + ': ' + votes + ' vote' + (votes === '1' ? '' : 's');

      tooltipEl.style.opacity = '1';
      var offset = 12;
      var parentRect = parent.getBoundingClientRect();
      var tooltipWidth = tooltipEl.offsetWidth || 0;
      var left = x + offset;
      if (left + tooltipWidth > parentRect.width) {
        left = x - offset - tooltipWidth;
      }
      if (left < 0) {
        left = 0;
      }
      tooltipEl.style.left = left + 'px';
      tooltipEl.style.top = (y + offset) + 'px';
    }

    function handleLeave() {
      tooltipEl.style.opacity = '0';
    }

    canvas.addEventListener('mousemove', handleMove);
    canvas.addEventListener('mouseleave', handleLeave);
  }

  function init() {
    var canvases = document.querySelectorAll('[data-sankey-chart]');
    if (!canvases.length) return;

    canvases.forEach(function (canvas) {
      var dataId = canvas.getAttribute('data-sankey-data-id');
      if (!dataId) return;
      var dataEl = document.getElementById(dataId);
      var flows = parseJson(dataEl);
      if (!flows.length) return;
      var electedId = canvas.getAttribute('data-sankey-elected-id');
      var electedEl = electedId ? document.getElementById(electedId) : null;
      var electedNodes = parseJson(electedEl);
      var eliminatedId = canvas.getAttribute('data-sankey-eliminated-id');
      var eliminatedEl = eliminatedId ? document.getElementById(eliminatedId) : null;
      var eliminatedNodes = parseJson(eliminatedEl);
      buildChart(canvas, flows, electedNodes, eliminatedNodes);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
