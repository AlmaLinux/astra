(function (window, document) {
  'use strict';

  function $(id) {
    return document.getElementById(id);
  }

  function setAction(value) {
    var el = $('send-mail-action');
    if (el) el.value = value;
  }

  function getField(id) {
    var el = $(id);
    return el ? String(el.value || '') : '';
  }

  function setRecipientCount(value) {
    var el = $('send-mail-recipient-count');
    if (!el) return;
    el.textContent = String(value == null ? '' : value);

    var sendCount = $('send-mail-send-count');
    if (sendCount) {
      sendCount.textContent = String(value == null ? '' : value);
    }
  }

  function setRecipientCountLoading() {
    var el = $('send-mail-recipient-count');
    if (!el) return;
    el.innerHTML = `
     <div class="spinner-border spinner-border-sm" style="vertical-align: middle;" role="status">
       <span class="sr-only">Loading...</span>
     </div>
    `;

    var sendCount = $('send-mail-send-count');
    if (sendCount) {
      sendCount.innerHTML = `
       <div class="spinner-border spinner-border-sm" style="vertical-align: middle;" role="status">
         <span class="sr-only">Loading...</span>
       </div>
      `;
    }
  }

  function parseCommaSeparated(raw) {
    // Match server-side parsing: accept commas, whitespace/newlines, or semicolons.
    var items = String(raw || '').trim().split(/[\s,;]+/);
    var out = [];
    for (var i = 0; i < items.length; i++) {
      var s = String(items[i] || '').trim();
      if (s) out.push(s);
    }
    return out;
  }

  function looksLikeEmail(s) {
    // Keep this conservative: client-side validation is UX help, server still validates.
    s = String(s || '').trim();
    if (!s) return false;
    if (s.indexOf(' ') >= 0) return false;
    // Basic "local@domain.tld" check.
    return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(s);
  }

  function validateManualEmails(raw) {
    var items = parseCommaSeparated(raw);
    var invalid = [];
    for (var i = 0; i < items.length; i++) {
      if (!looksLikeEmail(items[i])) invalid.push(items[i]);
    }
    return { items: items, invalid: invalid };
  }

  var previewSubmitTimer = null;
  function cancelScheduledPreviewSubmit() {
    if (!previewSubmitTimer) return;
    window.clearTimeout(previewSubmitTimer);
    previewSubmitTimer = null;
  }

  function schedulePreviewSubmit(delayMs) {
    var ms = typeof delayMs === 'number' ? delayMs : 250;
    cancelScheduledPreviewSubmit();
    previewSubmitTimer = window.setTimeout(function () {
      var form = $('send-mail-form');
      if (!form) return;
      setAction('preview');
      if (!validateRecipientsBeforeSubmit(null)) return;
      form.submit();
    }, ms);
  }

  function getCompose() {
    return window.TemplatedEmailCompose || null;
  }

  function getActiveRecipientModeFromTabs() {
    var tabs = document.querySelectorAll('#send-mail-recipient-tabs .nav-link');
    for (var i = 0; i < tabs.length; i++) {
      var t = tabs[i];
      if (t && t.classList && t.classList.contains('active')) {
        return String(t.getAttribute('data-recipient-mode') || '').trim();
      }
    }
    return '';
  }

  function syncRecipientModeFromTabs() {
    var modeEl = $('send-mail-recipient-mode');
    if (!modeEl) return;

    modeEl.value = getActiveRecipientModeFromTabs();
  }

  function activateRecipientTab(mode) {
    var wanted = String(mode || '').trim();
    if (!wanted) return;

    var tabs = document.querySelectorAll('#send-mail-recipient-tabs .nav-link');
    var target = null;
    for (var i = 0; i < tabs.length; i++) {
      var t = tabs[i];
      if (String(t.getAttribute('data-recipient-mode') || '').trim() === wanted) {
        target = t;
        break;
      }
    }
    if (!target) return;

    // Bootstrap 4 tabs are jQuery-powered. Use it if available.
    if (window.jQuery && window.jQuery.fn && window.jQuery.fn.tab) {
      window.jQuery(target).tab('show');
      return;
    }

    // Fallback: toggle classes manually.
    for (var j = 0; j < tabs.length; j++) {
      tabs[j].classList.remove('active');
    }
    target.classList.add('active');

    var panes = document.querySelectorAll('#send-mail-recipient-tab-content .tab-pane');
    for (var k = 0; k < panes.length; k++) {
      panes[k].classList.remove('active');
      panes[k].classList.remove('show');
    }
    var paneId = String(target.getAttribute('href') || '').replace(/^#/, '');
    var pane = paneId ? document.getElementById(paneId) : null;
    if (pane) {
      pane.classList.add('active');
      pane.classList.add('show');
    }
  }

  function hideRecipientsWarning(kind) {
    var box = $('send-mail-recipients-inline-warning');
    if (!box) return;
    if (kind && String(box.getAttribute('data-warning-kind') || '') !== String(kind || '')) {
      return;
    }
    box.classList.add('d-none');
    box.textContent = '';
    box.removeAttribute('data-warning-kind');
  }

  function showRecipientsWarning(message, kind) {
    var box = $('send-mail-recipients-inline-warning');
    if (!box) return;
    box.textContent = String(message || '');
    box.classList.remove('d-none');
    if (kind) {
      box.setAttribute('data-warning-kind', String(kind));
    } else {
      box.removeAttribute('data-warning-kind');
    }
  }

  function hasSavedCsvRecipients() {
    var el = $('send-mail-has-saved-csv');
    return !!(el && String(el.value || '') === '1');
  }

  function validateRecipientsBeforeSubmit(evt) {
    hideRecipientsWarning();
    syncRecipientModeFromTabs();

    // Validate CC/BCC regardless of recipient mode.
    var ccRaw = String(getField('id_cc') || '').trim();
    if (ccRaw) {
      var ccParsed = validateManualEmails(ccRaw);
      if (ccParsed.invalid.length) {
        if (evt && evt.preventDefault) evt.preventDefault();
        showRecipientsWarning('Invalid CC address(es): ' + ccParsed.invalid.join(', '), 'cc');
        return false;
      }
    }

    var bccRaw = String(getField('id_bcc') || '').trim();
    if (bccRaw) {
      var bccParsed = validateManualEmails(bccRaw);
      if (bccParsed.invalid.length) {
        if (evt && evt.preventDefault) evt.preventDefault();
        showRecipientsWarning('Invalid BCC address(es): ' + bccParsed.invalid.join(', '), 'bcc');
        return false;
      }
    }

    var replyToRaw = String(getField('id_reply_to') || '').trim();
    if (replyToRaw) {
      var replyToParsed = validateManualEmails(replyToRaw);
      if (replyToParsed.invalid.length) {
        if (evt && evt.preventDefault) evt.preventDefault();
        showRecipientsWarning('Invalid Reply-To address(es): ' + replyToParsed.invalid.join(', '), 'reply_to');
        return false;
      }
    }

    var modeEl = $('send-mail-recipient-mode');
    var mode = modeEl ? String(modeEl.value || '').trim() : '';

    if (mode === 'group') {
      var groupSelected = String(getField('id_group_cn') || '').trim() !== '';
      if (!groupSelected) {
        if (evt && evt.preventDefault) evt.preventDefault();
        showRecipientsWarning('Select a group, or switch to CSV recipients.');
        return false;
      }
      return true;
    }

    if (mode === 'csv') {
      var csvEl = $('id_csv_file');
      var csvSelected = false;
      if (csvEl) {
        if (csvEl.files && csvEl.files.length > 0) {
          csvSelected = true;
        } else {
          csvSelected = String(csvEl.value || '').trim() !== '';
        }
      }

      if (!csvSelected && !hasSavedCsvRecipients()) {
        if (evt && evt.preventDefault) evt.preventDefault();
        showRecipientsWarning('Upload a CSV, or switch to Group recipients.');
        return false;
      }

      return true;
    }

    if (mode === 'manual') {
      var raw = String(getField('id_manual_to') || '').trim();
      if (!raw) {
        if (evt && evt.preventDefault) evt.preventDefault();
        showRecipientsWarning('Add one or more email addresses, or switch to Group/CSV recipients.');
        return false;
      }

      var parsed = validateManualEmails(raw);
      if (parsed.invalid.length) {
        if (evt && evt.preventDefault) evt.preventDefault();
        showRecipientsWarning('Invalid email address(es): ' + parsed.invalid.join(', '));
        return false;
      }

      return true;
    }

    if (mode === 'users') {
      var usersEl = $('id_user_usernames');
      var hasUsers = false;
      if (usersEl) {
        if (usersEl.selectedOptions && usersEl.selectedOptions.length > 0) {
          hasUsers = true;
        } else {
          // Fallback: some browsers expose only `.value`.
          hasUsers = String(usersEl.value || '').trim() !== '';
        }
      }

      if (!hasUsers) {
        if (evt && evt.preventDefault) evt.preventDefault();
        showRecipientsWarning('Select one or more users, or switch to Group/CSV recipients.');
        return false;
      }

      return true;
    }

    if (evt && evt.preventDefault) evt.preventDefault();
    showRecipientsWarning('Choose Group, CSV, or Manual recipients first.');
    return false;
  }

  function onReady() {
    document.addEventListener('templated-email-compose:save-confirmed', function () {
      setAction('save');
      var form = $('send-mail-form');
      if (form) form.submit();
    });

    document.addEventListener('templated-email-compose:save-as-confirmed', function (e) {
      var detail = e && e.detail ? e.detail : {};
      var name = String((detail && detail.name) || '').trim();
      if (!name) return;
      $('send-mail-save-as-name').value = name;
      setAction('save_as');
      var form = $('send-mail-form');
      if (form) form.submit();
    });

    var composeBindingsReady = false;

    function bindComposeOnceReady() {
      if (composeBindingsReady) return;

      var compose = getCompose();
      if (!compose) {
        window.setTimeout(bindComposeOnceReady, 25);
        return;
      }

      composeBindingsReady = true;

      var templateSelect = compose.getTemplateSelectEl ? compose.getTemplateSelectEl() : null;

      // After a successful "Save as", the server re-renders with the new template
      // selected. The form fields already contain the correct content, so we only
      // need to set Restore/baseline so the user can start editing immediately.
      var autoloadEl = $('send-mail-autoload-template-id');
      if (autoloadEl) {
        var autoloadId = String(autoloadEl.value || '').trim();
        if (autoloadId) {
          if (compose.setTemplateId) {
            compose.setTemplateId(autoloadId);
          } else if (templateSelect) {
            templateSelect.value = autoloadId;
          }

          // Do NOT refetch the template JSON here: it would be async and could
          // overwrite edits if the user starts typing quickly.
          compose.setRestoreEnabled(true);
          compose.markBaseline(compose.getValues());
          if (window.TemplatedEmailComposePreview) {
            window.TemplatedEmailComposePreview.schedulePreviewRefresh(compose, 0);
          }
        }
      }
    }

    bindComposeOnceReady();

    // Note: the compose widget dispatches templated-email-compose:content-updated,
    // and we refresh preview off that signal.

    // Keep recipient_mode in sync with the tabs selection.
    var tabLinks = document.querySelectorAll('#send-mail-recipient-tabs .nav-link');

    function maybeAutoloadPreviewForActiveTab() {
      syncRecipientModeFromTabs();
      var modeEl = $('send-mail-recipient-mode');
      var mode = modeEl ? String(modeEl.value || '').trim() : '';

      if (mode === 'group') {
        var groupSelected = String(getField('id_group_cn') || '').trim();
        if (!groupSelected) return;
        setRecipientCountLoading();
        schedulePreviewSubmit(0);
        return;
      }

      if (mode === 'users') {
        var usersEl = $('id_user_usernames');
        var selectedCount = 0;
        if (usersEl && usersEl.selectedOptions && usersEl.selectedOptions.length != null) {
          selectedCount = usersEl.selectedOptions.length;
        } else if (usersEl) {
          selectedCount = String(usersEl.value || '').trim() ? 1 : 0;
        }
        if (!selectedCount) return;
        // Count is known client-side; keep it, but refresh preview/variables.
        schedulePreviewSubmit(0);
        return;
      }

      if (mode === 'manual') {
        var raw = String(getField('id_manual_to') || '');
        var parsed = validateManualEmails(raw);
        setRecipientCount(parsed.items.length);
        if (!raw.trim()) return;
        if (parsed.invalid.length) {
          showRecipientsWarning('Invalid email address(es): ' + parsed.invalid.join(', '));
          return;
        }
        hideRecipientsWarning();
        schedulePreviewSubmit(0);
        return;
      }

      if (mode === 'csv') {
        var csvEl = $('id_csv_file');
        var csvSelected = false;
        if (csvEl) {
          if (csvEl.files && csvEl.files.length > 0) {
            csvSelected = true;
          } else {
            csvSelected = String(csvEl.value || '').trim() !== '';
          }
        }
        if (!csvSelected && !hasSavedCsvRecipients()) return;
        setRecipientCountLoading();
        schedulePreviewSubmit(0);
      }
    }

    for (var i = 0; i < tabLinks.length; i++) {
      tabLinks[i].addEventListener('shown.bs.tab', syncRecipientModeFromTabs);
      tabLinks[i].addEventListener('shown.bs.tab', hideRecipientsWarning);
      tabLinks[i].addEventListener('shown.bs.tab', maybeAutoloadPreviewForActiveTab);
      tabLinks[i].addEventListener('click', hideRecipientsWarning);
      // Fallback for non-Bootstrap environments: run after click.
      tabLinks[i].addEventListener('click', function () {
        window.setTimeout(maybeAutoloadPreviewForActiveTab, 0);
      });
    }

    var loadFileBtn = $('send-mail-load-file-btn');
    if (loadFileBtn) {
      loadFileBtn.addEventListener('click', validateRecipientsBeforeSubmit);
    }

    function showSendConfirmModal() {
      var modalEl = $('send-mail-send-confirm-modal');
      if (!modalEl) return false;
      var jq = window.jQuery;
      if (jq && jq.fn && typeof jq.fn.modal === 'function') {
        jq(modalEl).modal('show');
        return true;
      }
      return false;
    }

    var sendBtn = $('send-mail-send-btn');
    if (sendBtn) {
      sendBtn.addEventListener('click', function (evt) {
        hideRecipientsWarning();
        cancelScheduledPreviewSubmit();

        // Validate current recipients config before even showing the modal.
        if (!validateRecipientsBeforeSubmit(evt)) return;

        // Prefer Bootstrap modal confirmation. If the modal isn't available,
        // proceed without blocking on browser confirm().
        if (!showSendConfirmModal()) {
          var form = $('send-mail-form');
          if (!form) return;
          setAction('send');
          form.submit();
        }
      });
    }

    var sendConfirmBtn = $('send-mail-send-confirm-btn');
    if (sendConfirmBtn) {
      sendConfirmBtn.addEventListener('click', function () {
        cancelScheduledPreviewSubmit();

        var form = $('send-mail-form');
        if (!form) return;
        setAction('send');

        // Validate again just in case fields changed while modal was open.
        if (!validateRecipientsBeforeSubmit(null)) return;
        form.submit();
      });
    }

    var groupEl = $('id_group_cn');
    if (groupEl) {
      groupEl.addEventListener('change', function () {
        hideRecipientsWarning();
        // Autoload recipients when group selection changes.
        var v = String(getField('id_group_cn') || '').trim();
        if (!v) return;
        setRecipientCountLoading();
        schedulePreviewSubmit(0);
      });
    }

    var csvEl = $('id_csv_file');
    if (csvEl) {
      csvEl.addEventListener('change', hideRecipientsWarning);
    }

    var manualEl = $('id_manual_to');
    if (manualEl) {
      function updateManualCountAndMaybePreview() {
        var raw = String(getField('id_manual_to') || '');
        var parsed = validateManualEmails(raw);
        setRecipientCount(parsed.items.length);

        if (!raw.trim()) {
          hideRecipientsWarning();
          return;
        }

        if (parsed.invalid.length) {
          showRecipientsWarning('Invalid email address(es): ' + parsed.invalid.join(', '));
          return;
        }

        hideRecipientsWarning();
        schedulePreviewSubmit(400);
      }

      manualEl.addEventListener('change', updateManualCountAndMaybePreview);
      manualEl.addEventListener('input', updateManualCountAndMaybePreview);
    }

    function validateCcOrBccField(id, kind, label) {
      var raw = String(getField(id) || '').trim();
      if (!raw) {
        hideRecipientsWarning(kind);
        return;
      }

      var parsed = validateManualEmails(raw);
      if (parsed.invalid.length) {
        showRecipientsWarning('Invalid ' + label + ' address(es): ' + parsed.invalid.join(', '), kind);
        return;
      }

      hideRecipientsWarning(kind);
    }

    var ccEl = $('id_cc');
    if (ccEl) {
      function validateCc() {
        validateCcOrBccField('id_cc', 'cc', 'CC');
      }
      ccEl.addEventListener('change', validateCc);
      ccEl.addEventListener('input', validateCc);
    }

    var bccEl = $('id_bcc');
    if (bccEl) {
      function validateBcc() {
        validateCcOrBccField('id_bcc', 'bcc', 'BCC');
      }
      bccEl.addEventListener('change', validateBcc);
      bccEl.addEventListener('input', validateBcc);
    }

    var usersEl = $('id_user_usernames');
    if (usersEl) {
      function updateUsersCountAndMaybePreview() {
        hideRecipientsWarning();
        var selectedCount = 0;
        if (usersEl.selectedOptions && usersEl.selectedOptions.length != null) {
          selectedCount = usersEl.selectedOptions.length;
        }
        setRecipientCount(selectedCount);

        if (selectedCount) {
          schedulePreviewSubmit(150);
        }
      }

      usersEl.addEventListener('change', updateUsersCountAndMaybePreview);

      var jq = window.jQuery;
      if (jq && jq.fn && jq.fn.select2) {
        try {
          jq(usersEl).select2({ width: '100%', closeOnSelect: false });
          jq(usersEl).on('change select2:select select2:unselect select2:clear', updateUsersCountAndMaybePreview);
        } catch (_e) {
          // Ignore Select2 init failures (page remains usable).
        }
      }
    }

    // Respect server-provided initial mode (e.g. deep links).
    var modeEl = $('send-mail-recipient-mode');
    var initialMode = modeEl ? String(modeEl.value || '').trim() : '';
    if (initialMode) {
      activateRecipientTab(initialMode);
    }

    syncRecipientModeFromTabs();

    // Initialize the "live" recipient count based on current input values.
    // Server count remains authoritative once a preview has been loaded.
    if (initialMode === 'manual') {
      var raw = String(getField('id_manual_to') || '');
      setRecipientCount(parseCommaSeparated(raw).length);
    }
    if (initialMode === 'users') {
      var uel = $('id_user_usernames');
      if (uel && uel.selectedOptions) setRecipientCount(uel.selectedOptions.length);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', onReady);
  } else {
    onReady();
  }
})(window, document);
