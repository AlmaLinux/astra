(function (window, document) {
  'use strict';

  function $(id) {
    return document.getElementById(id);
  }

  function getCompose() {
    return window.TemplatedEmailCompose || null;
  }

  function getField(name) {
    var compose = getCompose();
    return compose ? compose.getField(name) : '';
  }


  function scheduleEmailPreviewRefresh(compose, delayMs) {
    if (!window.TemplatedEmailComposePreview) return;
    window.TemplatedEmailComposePreview.schedulePreviewRefresh(compose || getCompose(), delayMs);
  }

  function markComposeBaselineFromFields() {
    var compose = getCompose();
    if (!compose) return;
    compose.setRestoreEnabled(true);
    compose.markBaseline(compose.getValues());
  }

  function electionIdFromPath() {
    var parts = String(window.location.pathname || '').split('/').filter(Boolean);
    // /elections/<id>/edit/
    if (parts.length >= 3 && parts[0] === 'elections' && parts[2] === 'edit') {
      var n = parseInt(parts[1], 10);
      return isFinite(n) ? n : null;
    }

    // /elections/0/edit/
    if (parts.length >= 2 && parts[0] === 'elections' && parts[1] === 'new') {
      return 0;
    }
    return null;
  }

  function eligibleUsersSearchUrlFromPath() {
    var electionId = electionIdFromPath();
    if (electionId == null) return null;
    return '/elections/' + encodeURIComponent(String(electionId)) + '/eligible-users/search/';
  }

  function eligibleVotersCountUrlFromPath() {
    var base = eligibleUsersSearchUrlFromPath();
    if (!base) return null;
    return base + '?count_only=1';
  }

  function collectSelectedCandidates() {
    var $ = window.jQuery;
    if (!$) return [];

    var out = [];
    var seen = {};

    $('select[name^="candidates-"][name$="-freeipa_username"]').each(function () {
      var $sel = $(this);
      var name = String($sel.attr('name') || '');
      var m = name.match(/^candidates-(\d+)-freeipa_username$/);
      if (!m) return;

      var idx = m[1];
      var del = $('input[name="candidates-' + idx + '-DELETE"]');
      if (del.length && del.prop('checked')) return;

      var v = $sel.val();
      if (!v) return;
      var u = String(v).trim();
      if (!u) return;
      if (seen[u]) return;
      seen[u] = true;

      var label = String($sel.find('option:selected').text() || '').trim();
      if (!label) label = u;
      out.push({ id: u, text: label });
    });

    out.sort(function (a, b) {
      return a.id.toLowerCase().localeCompare(b.id.toLowerCase());
    });
    return out;
  }

  function syncGroupCandidateOptions(root) {
    var $ = window.jQuery;
    if (!$) return;

    var elRoot = root || document;
    var candidates = collectSelectedCandidates();

    $(elRoot).find('select[name^="groups-"][name$="-candidate_usernames"]').each(function () {
      var $sel = $(this);
      var selected = $sel.val();
      if (!Array.isArray(selected)) selected = selected ? [selected] : [];

      var allowed = {};
      for (var i = 0; i < candidates.length; i++) {
        allowed[candidates[i].id] = candidates[i].text;
      }

      // Remove options that are no longer candidates.
      $sel.find('option').each(function () {
        var v = String(this.value || '').trim();
        if (!v) return;
        if (!Object.prototype.hasOwnProperty.call(allowed, v)) {
          this.remove();
        }
      });

      // Add options for all current candidates.
      for (var j = 0; j < candidates.length; j++) {
        var u = candidates[j].id;
        var text = candidates[j].text;
        if ($sel.find('option[value="' + u.replace(/"/g, '\\"') + '"]').length) continue;
        var opt = new window.Option(text, u, false, false);
        $sel.append(opt);
      }

      // Drop selections that no longer exist.
      var nextSelected = [];
      for (var k = 0; k < selected.length; k++) {
        var sv = String(selected[k] || '').trim();
        if (Object.prototype.hasOwnProperty.call(allowed, sv)) nextSelected.push(sv);
      }
      $sel.val(nextSelected);

      $sel.trigger('change');
    });
  }

  function normalizeUsername(value) {
    return String(value || '').trim().toLowerCase();
  }

  function clearSelectValue(selectEl) {
    if (!selectEl) return;
    var jq = window.jQuery;
    if (jq) {
      jq(selectEl).val(null).trigger('change');
      return;
    }
    selectEl.value = '';
    try {
      selectEl.dispatchEvent(new window.Event('change', { bubbles: true }));
    } catch (_e) {
      // Ignore unsupported browsers.
    }
  }

  function enforceNoSelfNomination(row) {
    if (!row || !row.querySelector) return;

    var candidateEl = row.querySelector('select[name$="-freeipa_username"]');
    var nominatorEl = row.querySelector('select[name$="-nominated_by"]');
    if (!candidateEl || !nominatorEl) return;

    var candidate = normalizeUsername(candidateEl.value);
    var nominator = normalizeUsername(nominatorEl.value);
    if (!candidate || !nominator || candidate !== nominator) return;

    clearSelectValue(nominatorEl);
  }

  async function saveTemplate(templateId) {
    if (!templateId) {
      try { window.alert('Select a template to save, or use Save as.'); } catch (_e) { /* noop */ }
      return;
    }

    var data = new window.FormData();
    data.append('email_template_id', String(templateId));
    data.append('subject', getField('subject'));
    data.append('html_content', getField('html_content'));
    data.append('text_content', getField('text_content'));

    try {
      var resp = await window.fetch(
        '/email-tools/templates/save/',
        {
          method: 'POST',
          headers: { 'X-CSRFToken': (getCompose() ? getCompose().getCsrfToken() : ''), 'Accept': 'application/json' },
          body: data
        }
      );
      var payload = await resp.json();
      if (!resp.ok || !payload || payload.ok !== true) {
        throw new Error(payload && payload.error ? payload.error : 'save failed');
      }
      markComposeBaselineFromFields();
      try { window.alert('Template saved.'); } catch (_e) { /* noop */ }
    } catch (_e) {
      try { window.alert('Failed to save template.'); } catch (__e) { /* noop */ }
    }
  }

  async function saveAsTemplate(name) {
    if (!name) return null;

    var data = new window.FormData();
    data.append('name', String(name));
    data.append('subject', getField('subject'));
    data.append('html_content', getField('html_content'));
    data.append('text_content', getField('text_content'));

    try {
      var resp = await window.fetch(
        '/email-tools/templates/save-as/',
        {
          method: 'POST',
          headers: { 'X-CSRFToken': (getCompose() ? getCompose().getCsrfToken() : ''), 'Accept': 'application/json' },
          body: data
        }
      );
      var payload = await resp.json();
      if (!resp.ok || !payload || payload.ok !== true) {
        throw new Error(payload && payload.error ? payload.error : 'save as failed');
      }
      return payload;
    } catch (_e) {
      return null;
    }
  }

  function initSelect2(root) {
    var $ = window.jQuery;
    if (!$ || !$.fn || !$.fn.select2) return;

    var elRoot = root || document;
    $(elRoot).find('select.alx-select2').each(function () {
      var $select = $(this);
      if ($select.data('alx-select2-initialized')) return;
      $select.data('alx-select2-initialized', true);

      var ajaxUrl = $select.attr('data-ajax-url') || $select.data('ajax-url');
      var startSourceId = $select.attr('data-start-datetime-source') || $select.data('start-datetime-source');

      var name = String($select.attr('name') || '');
      var id = String($select.attr('id') || '');
      var looksLikeEligibleUserSelect = Boolean(
        startSourceId ||
        /-freeipa_username$/.test(name) ||
        /-nominated_by$/.test(name) ||
        /-freeipa_username$/.test(id) ||
        /-nominated_by$/.test(id)
      );

      var looksLikeCandidateGroupSelect = Boolean(
        /-candidate_usernames$/.test(name) ||
        /-candidate_usernames$/.test(id)
      );

      // Exclusion-group candidate selection is handled as a plain <select multiple>.
      // Do not initialize Select2 for that field.
      if (looksLikeCandidateGroupSelect) {
        return;
      }

      if (!startSourceId && looksLikeEligibleUserSelect && document.getElementById('id_start_datetime')) {
        startSourceId = 'id_start_datetime';
      }

      // Newly-added formset rows can lose data-* attrs after template cloning/reparse.
      // Infer which selects should use the eligible-user search based on their field name.
      if (!ajaxUrl && looksLikeEligibleUserSelect) {
        ajaxUrl = eligibleUsersSearchUrlFromPath();
      }

      if (ajaxUrl) {
        var placeholder = String($select.attr('data-placeholder') || $select.data('placeholder') || '').trim();
        $select.select2({
          width: '100%',
          allowClear: true,
          placeholder: placeholder,
          minimumInputLength: 0,
          closeOnSelect: !$select.prop('multiple'),
          ajax: {
            url: ajaxUrl,
            dataType: 'json',
            delay: 200,
            data: function (params) {
              var payload = {
                q: params && params.term != null ? String(params.term) : ''
              };
              if (startSourceId) {
                var startEl = document.getElementById(String(startSourceId));
                if (startEl && startEl.value) payload.start_datetime = String(startEl.value);
              }

              var groupEl = document.getElementById('id_eligible_group_cn');
              if (groupEl && groupEl.value != null) {
                payload.eligible_group_cn = String(groupEl.value || '');
              }
              return payload;
            }
          }
        });

      } else {
        var placeholder2 = String($select.attr('data-placeholder') || $select.data('placeholder') || '').trim();
        $select.select2({ width: '100%', closeOnSelect: !$select.prop('multiple'), allowClear: true, placeholder: placeholder2 });
      }
    });
  }

  function addFormsetRow(prefix, emptyTemplateId, tbodyId) {
    var totalEl = $('id_' + prefix + '-TOTAL_FORMS');
    var tmpl = $(emptyTemplateId);
    var tbody = $(tbodyId);
    if (!totalEl || !tmpl || !tbody) return;

    var total = parseInt(String(totalEl.value || '0'), 10);
    if (!isFinite(total)) total = 0;

    var html = String(tmpl.innerHTML || '').replace(/__prefix__/g, String(total));

    var tmp = document.createElement('tbody');
    tmp.innerHTML = html;
    while (tmp.firstElementChild) {
      tbody.appendChild(tmp.firstElementChild);
    }

    totalEl.value = String(total + 1);
    initSelect2(tbody);
    syncGroupCandidateOptions(document);
  }

  async function refreshEligibleVotersCount() {
    var url = eligibleVotersCountUrlFromPath();
    if (!url) return;

    var startEl = document.getElementById('id_start_datetime');
    var startValue = startEl && startEl.value ? String(startEl.value) : '';
    var groupEl = document.getElementById('id_eligible_group_cn');
    var groupValue = groupEl && groupEl.value != null ? String(groupEl.value || '') : '';

    var fullUrl = url;
    if (startValue) fullUrl += '&start_datetime=' + encodeURIComponent(startValue);
    fullUrl += '&eligible_group_cn=' + encodeURIComponent(groupValue);

    try {
      var resp = await window.fetch(fullUrl, { headers: { 'Accept': 'application/json' } });
      if (!resp.ok) return;
      var payload = await resp.json();
      if (!payload || typeof payload.count !== 'number') return;

      var nodes = document.querySelectorAll('.js-eligible-voters-count');
      for (var i = 0; i < nodes.length; i++) {
        nodes[i].textContent = String(payload.count);
      }
    } catch (_e) {
      // Ignore transient failures.
    }
  }

  async function isUsernameEligibleForCurrentGroup(username) {
    username = String(username || '').trim();
    if (!username) return false;

    var base = eligibleUsersSearchUrlFromPath();
    if (!base) return true;

    var startEl = document.getElementById('id_start_datetime');
    var startValue = startEl && startEl.value ? String(startEl.value) : '';
    var groupEl = document.getElementById('id_eligible_group_cn');
    var groupValue = groupEl && groupEl.value != null ? String(groupEl.value || '') : '';

    var url = base + '?q=' + encodeURIComponent(username);
    if (startValue) url += '&start_datetime=' + encodeURIComponent(startValue);
    url += '&eligible_group_cn=' + encodeURIComponent(groupValue);

    try {
      var resp = await window.fetch(url, { headers: { 'Accept': 'application/json' } });
      if (!resp.ok) return true;
      var payload = await resp.json();
      var results = payload && payload.results ? payload.results : [];
      if (!Array.isArray(results)) return true;

      for (var i = 0; i < results.length; i++) {
        if (results[i] && String(results[i].id || '').trim() === username) return true;
      }
      return false;
    } catch (_e) {
      // Degrade gracefully; don't auto-clear if the server can't be reached.
      return true;
    }
  }

  async function clearIneligibleSelectedCandidates() {
    var jq = window.jQuery;
    if (!jq) return;

    var selects = jq('select[name^="candidates-"][name$="-freeipa_username"]');
    if (!selects.length) return;

    for (var i = 0; i < selects.length; i++) {
      var $sel = jq(selects[i]);
      var current = $sel.val();
      if (!current) continue;

      var username = String(current || '').trim();
      if (!username) continue;

      var ok = await isUsernameEligibleForCurrentGroup(username);
      if (!ok) {
        $sel.val(null).trigger('change');
      }
    }
  }

  function markRowDeleted(row) {
    if (!row) return;
    var del = row.querySelector('input[name$="-DELETE"]');
    if (del) {
      // Django formset DELETE fields are typically checkboxes, but treat them generically.
      del.checked = true;
      del.value = 'on';
      try {
        del.dispatchEvent(new window.Event('change', { bubbles: true }));
      } catch (_e) {
        // IE11-era fallback not needed; ignore.
      }
    }
    row.style.display = 'none';
  }

  function initDateTimePickers() {
    var jq = window.jQuery;
    if (!jq || !jq.fn) return;
    if (typeof jq.fn.datetimepicker !== 'function') return;

    jq('.js-datetime-picker').each(function () {
      var $el = jq(this);
      if ($el.data('datetimepicker')) return;
      $el.datetimepicker({
        icons: {
          time: 'far fa-clock',
          date: 'far fa-calendar',
          up: 'fas fa-arrow-up',
          down: 'fas fa-arrow-down',
          previous: 'fas fa-chevron-left',
          next: 'fas fa-chevron-right',
          today: 'far fa-calendar-check',
          clear: 'far fa-trash-alt',
          close: 'far fa-times-circle'
        }
      });
    });
  }

  function onReady() {
    function resetEmailSaveMode() {
      var mode = $('election-edit-email-save-mode');
      if (mode) mode.value = '';
    }

    var compose = getCompose();
    var templateSelect = compose && compose.getTemplateSelectEl ? compose.getTemplateSelectEl() : null;
    if (templateSelect) {
      templateSelect.addEventListener('change', function () {
        resetEmailSaveMode();
        var id = String(templateSelect.value || '').trim();
        if (!id) return;
        // Loading is handled by the shared compose widget.
        scheduleEmailPreviewRefresh(getCompose(), 50);
      });
    }

    document.addEventListener('templated-email-compose:save-confirmed', function (e) {
      var compose2 = window.TemplatedEmailComposePreview ? window.TemplatedEmailComposePreview.getComposeFromEvent(e) : getCompose();
      var id = compose2 && compose2.getTemplateId ? compose2.getTemplateId() : (templateSelect ? String(templateSelect.value || '').trim() : '');
      saveTemplate(id);
    });

    document.addEventListener('templated-email-compose:save-as-confirmed', async function (e) {
      var detail = e && e.detail ? e.detail : {};
      var name = String((detail && detail.name) || '').trim();
      if (!name) return;

      var payload = await saveAsTemplate(name);
      if (!payload) {
        try { window.alert('Failed to create template.'); } catch (_e) { /* noop */ }
        return;
      }

      if (templateSelect && payload.id != null) {
        var opt = new window.Option(String(payload.name || name), String(payload.id), true, true);
        templateSelect.appendChild(opt);
        templateSelect.value = String(payload.id);
      }

      // If the user explicitly created a new template, they almost certainly
      // intend to save it onto the election draft too.
      var saveModeEl = $('election-edit-email-save-mode');
      if (saveModeEl) saveModeEl.value = 'save';

      markComposeBaselineFromFields();
      scheduleEmailPreviewRefresh(getCompose(), 50);
      try { window.alert('Template created.'); } catch (_e) { /* noop */ }
    });

    // Make restore meaningful even before the user loads a template.
    markComposeBaselineFromFields();
    initDateTimePickers();

    syncGroupCandidateOptions(document);
    initSelect2(document);

    var jq = window.jQuery;

    // Select2 triggers jQuery events; using jQuery handlers here ensures the
    // eligible-voter count refreshes when the group is changed/cleared.
    var groupEl = document.getElementById('id_eligible_group_cn');
    if (groupEl) {
      if (jq) {
        jq(groupEl).on('change select2:select select2:clear', function () {
          refreshEligibleVotersCount();
          clearIneligibleSelectedCandidates();
        });
      } else {
        groupEl.addEventListener('change', function () {
          refreshEligibleVotersCount();
          clearIneligibleSelectedCandidates();
        });
      }
    }

    var startEl = document.getElementById('id_start_datetime');
    if (startEl) {
      if (jq) {
        jq(startEl).on('change', function () {
          refreshEligibleVotersCount();
        });
      } else {
        startEl.addEventListener('change', function () {
          refreshEligibleVotersCount();
        });
      }
    }

    refreshEligibleVotersCount();
    if (jq) {
      jq(document).on('change', 'select[name^="candidates-"][name$="-freeipa_username"], input[name^="candidates-"][name$="-DELETE"]', function () {
        syncGroupCandidateOptions(document);
        var row = this && this.closest ? this.closest('tr') : null;
        if (!row) row = jq(this).closest('tr')[0] || null;
        enforceNoSelfNomination(row);
      });
      jq(document).on('change', 'select[name^="candidates-"][name$="-nominated_by"]', function () {
        var row = this && this.closest ? this.closest('tr') : null;
        if (!row) row = jq(this).closest('tr')[0] || null;
        enforceNoSelfNomination(row);
      });
    } else {
      document.addEventListener('change', function (e) {
        var target = e && e.target ? e.target : null;
        if (!target || !target.name) return;
        var name = String(target.name || '');
        if (!/^candidates-\d+-freeipa_username$/.test(name) && !/^candidates-\d+-nominated_by$/.test(name)) {
          return;
        }
        var row = target.closest ? target.closest('tr') : null;
        enforceNoSelfNomination(row);
      });
    }

    var addCandidateBtn = $('candidates-add-row');
    if (addCandidateBtn) {
      addCandidateBtn.addEventListener('click', function (e) {
        e.preventDefault();
        addFormsetRow('candidates', 'candidates-empty-form', 'candidates-formset-body');
      });
    }

    var addGroupBtn = $('groups-add-row');
    if (addGroupBtn) {
      addGroupBtn.addEventListener('click', function (e) {
        e.preventDefault();
        addFormsetRow('groups', 'groups-empty-form', 'groups-formset-body');
      });
    }

    // Prefer jQuery delegation because it already exists on this page and is
    // robust across dynamic formset rows.
    if (jq) {
      jq(document).on('click', '.election-edit-remove-row', function (e) {
        e.preventDefault();
        var row = null;
        if (this && this.closest) {
          row = this.closest('tr');
        }
        if (!row) {
          row = jq(this).closest('tr')[0] || null;
        }
        markRowDeleted(row);
      });
    } else {
      document.addEventListener('click', function (e) {
        var t = e.target;
        if (!t) return;
        var btn = null;
        if (t.classList && t.classList.contains('election-edit-remove-row')) {
          btn = t;
        } else if (t.closest) {
          btn = t.closest('.election-edit-remove-row');
        }
        if (!btn) return;

        e.preventDefault();
        var row = btn.closest ? btn.closest('tr') : null;
        markRowDeleted(row);
      });
    }

    var form = $('election-edit-form');
    if (form) {
      form.addEventListener('submit', function (e) {
        var actionEl = $('election-edit-action');
        var action = actionEl ? String(actionEl.value || '') : '';
        if (action !== 'save_draft') return;

        var hasElection = String(($('election-edit-has-election') || {}).value || '') === '1';
        var status = String(($('election-edit-election-status') || {}).value || '');
        if (!hasElection || status !== 'draft') return;

        var origId = String(($('election-edit-original-email-template-id') || {}).value || '').trim();
        var currentId = templateSelect ? String(templateSelect.value || '').trim() : '';
        var saveModeEl = $('election-edit-email-save-mode');
        var saveMode = saveModeEl ? String(saveModeEl.value || '').trim() : '';

        if (origId !== currentId && !saveMode) {
          e.preventDefault();

          var jq = window.jQuery;
          if (jq && jq.fn && typeof jq.fn.modal === 'function') {
            jq('#edit-template-changed-modal').modal('show');
            return;
          }

          // If Bootstrap modal isn't available, be conservative.
          if (saveModeEl) saveModeEl.value = 'keep_existing';
          form.submit();
        }
      });
    }

    var keepBtn = $('edit-keep-existing-email-btn');
    if (keepBtn) {
      keepBtn.addEventListener('click', function (e) {
        e.preventDefault();
        var saveModeEl = $('election-edit-email-save-mode');
        if (saveModeEl) saveModeEl.value = 'keep_existing';
        var jq = window.jQuery;
        if (jq && jq.fn && typeof jq.fn.modal === 'function') jq('#edit-template-changed-modal').modal('hide');
        if (form) form.submit();
      });
    }

    var saveBtn = $('edit-save-email-btn');
    if (saveBtn) {
      saveBtn.addEventListener('click', function (e) {
        e.preventDefault();
        var saveModeEl = $('election-edit-email-save-mode');
        if (saveModeEl) saveModeEl.value = 'save';
        var jq = window.jQuery;
        if (jq && jq.fn && typeof jq.fn.modal === 'function') jq('#edit-template-changed-modal').modal('hide');
        if (form) form.submit();
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', onReady);
  } else {
    onReady();
  }
})(window, document);
