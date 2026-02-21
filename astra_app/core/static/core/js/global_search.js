/**
 * Global search form handler for the navbar.
 *
 * Reads configuration from a JSON script tag (#global-search-config) to
 * determine whether the current user has directory access (users + groups
 * search) or groups-only search.
 *
 * The search input (#global-search-input) fires debounced AJAX requests
 * to /search/ and renders results in the dropdown (#global-search-menu).
 */
(function () {
  'use strict';

  var input = document.getElementById('global-search-input');
  var menu = document.getElementById('global-search-menu');
  if (!input || !menu) return;

  // Read the directory-access flag from the inline JSON config rendered
  // by the Django template.  Falls back to false (groups-only search).
  var configEl = document.getElementById('global-search-config');
  var config = {};
  try {
    config = configEl ? JSON.parse(configEl.textContent || '{}') : {};
  } catch (_e) {
    config = {};
  }
  var hasDirectoryAccess = !!config.hasDirectoryAccess;

  var debounceTimer = null;
  var lastQuery = '';
  var inflight = null;

  function hideMenu() {
    menu.classList.remove('show');
    menu.innerHTML = '';
  }

  function showMenu() {
    menu.classList.add('show');
  }

  // Applied to every text-bearing div in the results so nothing overflows the box.
  var TRUNC = 'style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"';

  function escapeHtml(s) {
    return String(s)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function renderLoading() {
    var html = '';
    if (hasDirectoryAccess) {
      html += '<h6 class="dropdown-header">Users</h6>' +
              '<div class="dropdown-item text-muted"><i class="fas fa-circle-notch fa-spin mr-2"></i>Searching\u2026</div>' +
              '<div class="dropdown-divider"></div>';
    }
    html += '<h6 class="dropdown-header">Groups</h6>' +
            '<div class="dropdown-item text-muted"><i class="fas fa-circle-notch fa-spin mr-2"></i>Searching\u2026</div>';
    if (hasDirectoryAccess) {
      html += '<div class="dropdown-divider"></div>' +
              '<h6 class="dropdown-header">Organizations</h6>' +
              '<div class="dropdown-item text-muted"><i class="fas fa-circle-notch fa-spin mr-2"></i>Searching\u2026</div>';
    }
    menu.innerHTML = html;
    showMenu();
  }

  function renderResults(q, data) {
    var users = Array.isArray(data.users) ? data.users : [];
    var groups = Array.isArray(data.groups) ? data.groups : [];

    var html = '';
    if (hasDirectoryAccess) {
      html += '<h6 class="dropdown-header">Users</h6>';
      if (users.length === 0) {
        html += '<div class="dropdown-item text-muted">No users found</div>';
      } else {
        for (var i = 0; i < users.length; i++) {
          var u = users[i];
          var username = escapeHtml(u.username || '');
          var fullName = escapeHtml(u.full_name || '');
          html += '<a class="dropdown-item" href="/user/' + encodeURIComponent(u.username) + '/">' +
                  '<div class="font-weight-bold" ' + TRUNC + '>' + username + '</div>' +
                  (fullName ? '<div class="text-muted text-sm" ' + TRUNC + '>' + fullName + '</div>' : '') +
                  '</a>';
        }
      }

      html += '<div class="dropdown-divider"></div>';
    }
    html += '<h6 class="dropdown-header">Groups</h6>';
    if (groups.length === 0) {
      html += '<div class="dropdown-item text-muted">No groups found</div>';
    } else {
      for (var j = 0; j < groups.length; j++) {
        var g = groups[j];
        var cn = escapeHtml(g.cn || '');
        var desc = escapeHtml(g.description || '');
        html += '<a class="dropdown-item" href="/group/' + encodeURIComponent(g.cn) + '/">' +
                '<div class="font-weight-bold" ' + TRUNC + '>' + cn + '</div>' +
                (desc ? '<div class="text-muted text-sm" ' + TRUNC + '>' + desc + '</div>' : '') +
                '</a>';
      }
    }

    if (hasDirectoryAccess) {
      var orgs = Array.isArray(data.orgs) ? data.orgs : [];
      html += '<div class="dropdown-divider"></div>';
      html += '<h6 class="dropdown-header">Organizations</h6>';
      if (orgs.length === 0) {
        html += '<div class="dropdown-item text-muted">No organizations found</div>';
      } else {
        for (var k = 0; k < orgs.length; k++) {
          var o = orgs[k];
          var orgName = escapeHtml(o.name || '');
          html += '<a class="dropdown-item" href="/organization/' + encodeURIComponent(o.id) + '/">' +
                  '<div class="font-weight-bold" ' + TRUNC + '>' + orgName + '</div>' +
                  '</a>';
        }
      }
    }

    menu.innerHTML = html;
    showMenu();
  }

  async function doSearch(q) {
    if (!q) {
      hideMenu();
      return;
    }
    lastQuery = q;
    renderLoading();

    if (inflight && typeof inflight.abort === 'function') {
      inflight.abort();
    }
    inflight = new AbortController();

    try {
      var resp = await fetch('/search/?q=' + encodeURIComponent(q), {
        headers: { 'Accept': 'application/json' },
        signal: inflight.signal,
      });
      if (!resp.ok) {
        hideMenu();
        return;
      }
      var data = await resp.json();
      if (q !== lastQuery) return;
      renderResults(q, data);
    } catch (e) {
      // AbortError is expected on rapid typing.
      if (e && e.name === 'AbortError') return;
      hideMenu();
    }
  }

  input.addEventListener('input', function () {
    var q = (input.value || '').trim();
    if (!q) {
      hideMenu();
      return;
    }
    if (debounceTimer) window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(function () { doSearch(q); }, 180);
  });

  input.addEventListener('focus', function () {
    var q = (input.value || '').trim();
    if (q) doSearch(q);
  });

  document.addEventListener('click', function (evt) {
    if (!menu.classList.contains('show')) return;
    var target = evt.target;
    if (target === input || menu.contains(target)) return;
    hideMenu();
  });

  document.addEventListener('keydown', function (evt) {
    if (evt.key === 'Escape') {
      hideMenu();
      input.blur();
    }
  });
})();
