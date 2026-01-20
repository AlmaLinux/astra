(function (window, document) {
  'use strict';

  // Compose widget + helpers. This file is intended to be shared by Send Mail,
  // Elections, and Template editing to avoid duplicated JS and keep fixes centralized.
  if (window.TemplatedEmailComposeRegistry && window.TemplatedEmailComposeRegistry._composeLibLoaded) return;

  if (!window.TemplatedEmailComposeRegistry) {
    window.TemplatedEmailComposeRegistry = {
      instances: [],
      getDefault: function () {
        return this.instances.length ? this.instances[0] : null;
      },
      getAll: function () {
        return this.instances.slice();
      }
    };
  }

  window.TemplatedEmailComposeRegistry._composeLibLoaded = true;

  function dispatch(name, detail) {
    // Some environments still lack `new CustomEvent(...)` (or implement it
    // differently). Keep this robust so a dispatch failure doesn't kill the
    // entire compose widget.
    var evt = null;
    try {
      evt = new CustomEvent(name, { detail: detail, bubbles: true });
    } catch (_e1) {
      try {
        evt = document.createEvent('CustomEvent');
        evt.initCustomEvent(name, true, true, detail);
      } catch (_e2) {
        try {
          evt = document.createEvent('Event');
          evt.initEvent(name, true, true);
          evt.detail = detail;
        } catch (_e3) {
          evt = null;
        }
      }
    }

    if (evt) {
      document.dispatchEvent(evt);
    }
  }

  function q(container, selector) {
    if (!container) return null;
    return container.querySelector(selector);
  }

  function mustacheOverlay() {
    return {
      token: function (stream) {
        if (stream.match('{{')) {
          var ch;
          while ((ch = stream.next()) != null) {
            if (ch === '}' && stream.match('}')) break;
          }
          return 'mustache';
        }
        while (stream.next() != null && !stream.match('{{', false)) {}
        return null;
      }
    };
  }

  function normalizeText(s) {
    return String(s || '')
      .replace(/\r\n/g, '\n')
      .replace(/\r/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  function htmlToPlainText(html) {
    // Normalize a common signature block into a conventional plain-text signature.
    // This avoids rendering it as emphasized text ("*The AlmaLinux Team*").
    var rawHtml = String(html || '').replace(
      /<p>\s*<em>\s*The AlmaLinux Team\s*<\/em>\s*<\/p>/gi,
      '\n<p>-- The AlmaLinux Team</p>'
    );

    // Drop Django template tags from the HTML->text conversion.
    // These directives are not meaningful in plain text and can leak confusing
    // artifacts like "{% load ... %}" into the generated text.
    rawHtml = rawHtml.replace(/{%[\s\S]*?%}/g, '');
    var doc = null;
    try {
      doc = new window.DOMParser().parseFromString(rawHtml, 'text/html');
    } catch (_e) {
      doc = null;
    }
    if (!doc || !doc.body) return normalizeText(rawHtml);

    function collapseInlineWhitespace(s) {
      return String(s || '').replace(/\s+/g, ' ').trim();
    }

    function renderTextNodeValue(value) {
      // Preserve leading/trailing whitespace as a single space so we don't
      // accidentally glue formatting markers to surrounding words.
      // Example: "election: <strong>X</strong>" should become "election: **X**".
      var raw = String(value || '');
      if (!raw) return '';

      var hasLeading = /^\s/.test(raw);
      var hasTrailing = /\s$/.test(raw);
      var core = raw.replace(/\s+/g, ' ').trim();

      if (!core) return ' ';

      var out = core;
      if (hasLeading) out = ' ' + out;
      if (hasTrailing) out = out + ' ';
      return out;
    }

    function joinNonEmpty(parts, sep) {
      var out = [];
      for (var i = 0; i < parts.length; i++) {
        var p = String(parts[i] || '');
        if (p) out.push(p);
      }
      return out.join(sep);
    }

    function renderInline(node) {
      if (!node) return '';
      if (node.nodeType === 3) {
        return renderTextNodeValue(node.nodeValue || '');
      }
      if (node.nodeType !== 1) {
        return '';
      }

      var tag = String(node.tagName || '').toUpperCase();
      if (tag === 'BR') return '\n';
      if (tag === 'IMG') return '';

      var children = [];
      for (var i = 0; i < node.childNodes.length; i++) {
        children.push(renderInline(node.childNodes[i]));
      }
      var inner = joinNonEmpty(children, '');

      if (tag === 'B' || tag === 'STRONG') {
        return inner ? ('**' + inner + '**') : '';
      }
      if (tag === 'I' || tag === 'EM') {
        return inner ? ('*' + inner + '*') : '';
      }
      if (tag === 'U') {
        return inner ? ('_' + inner + '_') : '';
      }
      if (tag === 'A') {
        var href = '';
        try {
          href = String(node.getAttribute('href') || '').trim();
        } catch (_e) {
          href = '';
        }

        var text = inner;
        if (!href) return text;
        if (!text || text === href) return href;
        return '[' + text + '](' + href + ')';
      }

      return inner;
    }

    function prefixLines(text, prefix) {
      var lines = String(text || '').split(/\n/);
      var out = [];
      for (var i = 0; i < lines.length; i++) {
        var line = String(lines[i] || '').replace(/\s+$/, '');
        if (!line) {
          out.push('');
          continue;
        }
        out.push(prefix + line);
      }
      return out.join('\n');
    }

    function renderBlock(node) {
      if (!node) return '';
      if (node.nodeType === 3) {
        return collapseInlineWhitespace(node.nodeValue || '');
      }
      if (node.nodeType !== 1) {
        return '';
      }

      var tag = String(node.tagName || '').toUpperCase();
      if (tag === 'BR') return '\n';
      if (tag === 'HR') return '\n\n---\n';

      if (/^H[1-6]$/.test(tag)) {
        var level = parseInt(tag.substring(1), 10);
        if (!isFinite(level) || level < 1) level = 1;
        if (level > 6) level = 6;
        return '#'.repeat(level) + ' ' + collapseInlineWhitespace(renderInline(node)) + '\n\n';
      }

      if (tag === 'BLOCKQUOTE') {
        var quoted = renderBlockChildren(node);
        quoted = normalizeText(quoted);
        if (!quoted) return '';
        return prefixLines(quoted, '> ') + '\n\n';
      }

      if (tag === 'LI') {
        var liText = collapseInlineWhitespace(renderInline(node));
        return liText ? ('- ' + liText + '\n') : '';
      }

      if (tag === 'UL' || tag === 'OL') {
        var items = [];
        for (var i = 0; i < node.childNodes.length; i++) {
          items.push(renderBlock(node.childNodes[i]));
        }
        var joined = joinNonEmpty(items, '');
        return joined ? (joined + '\n') : '';
      }

      if (tag === 'P' || tag === 'DIV') {
        var para = collapseInlineWhitespace(renderInline(node));
        return para ? (para + '\n\n\n') : '';
      }

      if (tag === 'PRE' || tag === 'CODE') {
        // Keep code blocks readable for plain-text emails.
        var codeText = String(node.textContent || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
        return codeText ? (codeText + '\n\n') : '';
      }

      return renderBlockChildren(node);
    }

    function renderBlockChildren(node) {
      var parts = [];
      for (var i = 0; i < node.childNodes.length; i++) {
        var child = node.childNodes[i];
        if (child && child.nodeType === 3) {
          // Whitespace-only text nodes at block boundaries are just HTML
          // formatting/indentation; dropping them avoids stray leading spaces in
          // the rendered plain text.
          if (/^\s*$/.test(String(child.nodeValue || ''))) continue;
        }
        var childTag = child && child.tagName ? String(child.tagName).toUpperCase() : '';
        var isBlock = childTag && (childTag === 'P' || childTag === 'DIV' || childTag === 'UL' || childTag === 'OL' || childTag === 'LI' || childTag === 'BLOCKQUOTE' || childTag === 'HR' || /^H[1-6]$/.test(childTag));
        parts.push(isBlock ? renderBlock(child) : renderInline(child));
      }
      return joinNonEmpty(parts, '');
    }

    return normalizeText(renderBlock(doc.body));
  }

  function initCompose(container) {
    if (!container) return null;
    if (container.getAttribute('data-compose-initialized') === '1') return null;
    container.setAttribute('data-compose-initialized', '1');

    var baseline = null;
    var editors = null;

    function getCurrentValues() {
      var subjectEl = q(container, '[name="subject"]');
      var htmlEl = q(container, 'textarea[name="html_content"]');
      var textEl = q(container, 'textarea[name="text_content"]');
      return {
        subject: String((subjectEl && subjectEl.value) || ''),
        html: String((htmlEl && htmlEl.value) || ''),
        text: String((textEl && textEl.value) || ''),
      };
    }

    function setBaselineToCurrent() {
      baseline = getCurrentValues();
    }

    function isDirty() {
      if (!baseline) setBaselineToCurrent();
      if (!baseline) return false;

      var current = getCurrentValues();
      return current.subject !== baseline.subject || current.html !== baseline.html || current.text !== baseline.text;
    }

    function updateUnsavedBadge() {
      var badge = q(container, '[data-compose-unsaved-badge]');
      if (!badge) return;
      if (isDirty()) {
        badge.classList.remove('d-none');
      } else {
        badge.classList.add('d-none');
      }
    }

    function initCodeMirror() {
      var htmlTa = q(container, 'textarea[name="html_content"]');
      var textTa = q(container, 'textarea[name="text_content"]');
      if (htmlTa) htmlTa.setAttribute('spellcheck', 'true');
      if (textTa) textTa.setAttribute('spellcheck', 'true');
      if (!window.CodeMirror || !htmlTa || !textTa) return;

      function ensureHtmlLintWarning(message) {
        var existing = q(container, '[data-compose-html-lint-warning]');
        if (existing) return;

        var label = null;
        try {
          label = container.querySelector('label[for="id_html_content"]');
        } catch (_e0) {
          label = null;
        }
        if (!label || !label.parentNode) return;

        var node = null;
        try {
          node = document.createElement('div');
        } catch (_e1) {
          node = null;
        }
        if (!node) return;

        node.setAttribute('data-compose-html-lint-warning', '1');
        node.className = 'text-muted small';
        node.style.marginTop = '-0.25rem';
        node.textContent = String(message || 'HTML linting is unavailable.');
        label.parentNode.insertBefore(node, label.nextSibling);
      }

      function getHtmlLintHelper() {
        // The html-lint addon registers a helper under "html".
        // When using htmlmixed mode, cm.getHelper("lint") may or may not
        // resolve depending on the mode; this keeps linting reliable.
        try {
          if (window.CodeMirror && window.CodeMirror.helpers && window.CodeMirror.helpers.lint) {
            return window.CodeMirror.helpers.lint.html || null;
          }
        } catch (_e) {
          // Ignore.
        }
        return null;
      }

      var cmSpellcheckOpts = {
        // Enables native browser spellcheck (red underline) while keeping
        // CodeMirror syntax highlighting via the editor mode.
        inputStyle: 'contenteditable',
        spellcheck: true,
        autocorrect: true,
        autocapitalize: true,
      };

      var htmlEditor = window.CodeMirror.fromTextArea(htmlTa, {
        mode: 'htmlmixed',
        theme: 'mdn-like',
        lineNumbers: false,
        lineWrapping: true,
        gutters: ['CodeMirror-lint-markers'],
        lint: {
          getAnnotations: getHtmlLintHelper() || null,
          selfContain: true,
          highlightLines: true,
        },
        inputStyle: cmSpellcheckOpts.inputStyle,
        spellcheck: cmSpellcheckOpts.spellcheck,
        autocorrect: cmSpellcheckOpts.autocorrect,
        autocapitalize: cmSpellcheckOpts.autocapitalize,
      });

      // The CodeMirror html-lint addon depends on a global HTMLHint.
      // If it's missing, lint.js will silently do nothing (and html-lint.js logs
      // an error). Provide a small inline hint so this is discoverable.
      if (getHtmlLintHelper() && !window.HTMLHint) {
        ensureHtmlLintWarning('HTML linting is enabled but HTMLHint is not loaded.');
      }

      var textEditor = window.CodeMirror.fromTextArea(textTa, {
        mode: 'text/plain',
        theme: 'mdn-like',
        lineNumbers: false,
        lineWrapping: true,
        inputStyle: cmSpellcheckOpts.inputStyle,
        spellcheck: cmSpellcheckOpts.spellcheck,
        autocorrect: cmSpellcheckOpts.autocorrect,
        autocapitalize: cmSpellcheckOpts.autocapitalize,
      });

      htmlEditor.addOverlay(mustacheOverlay());
      textEditor.addOverlay(mustacheOverlay());

      htmlEditor.on('change', function () {
        htmlEditor.save();
        updateUnsavedBadge();
        dispatch('templated-email-compose:content-updated', { instance: api });
      });
      textEditor.on('change', function () {
        textEditor.save();
        updateUnsavedBadge();
        dispatch('templated-email-compose:content-updated', { instance: api });
      });

      editors = { html: htmlEditor, text: textEditor };

      var copyBtn = q(container, '[data-compose-action="copy-html-to-text"]');
      if (copyBtn) {
        copyBtn.addEventListener('click', function (e) {
          e.preventDefault();
          var html = htmlEditor.getValue();
          var text = htmlToPlainText(html);
          textEditor.setValue(text);
          textEditor.save();
          updateUnsavedBadge();
          dispatch('templated-email-compose:content-updated', { instance: api });
        });
      }
    }

    function getField(name) {
      var el = q(container, '[name="' + String(name) + '"]');
      if (!el) return '';
      return String(el.value || '');
    }

    function setField(name, value) {
      var el = q(container, '[name="' + String(name) + '"]');
      if (!el) return;
      el.value = value == null ? '' : String(value);

      if (editors) {
        if (name === 'html_content' && editors.html && typeof editors.html.setValue === 'function') {
          editors.html.setValue(String(el.value || ''));
          if (typeof editors.html.save === 'function') editors.html.save();
        }
        if (name === 'text_content' && editors.text && typeof editors.text.setValue === 'function') {
          editors.text.setValue(String(el.value || ''));
          if (typeof editors.text.save === 'function') editors.text.save();
        }
      }

      dispatch('templated-email-compose:content-updated', { instance: api });
    }

    function setValues(values) {
      values = values || {};
      setField('subject', values.subject);
      setField('html_content', values.html);
      setField('text_content', values.text);
    }

    function setRestoreEnabled(on) {
      var btn = q(container, '[data-compose-action="restore"]');
      if (!btn) return;
      btn.disabled = !on;
    }

    function markBaseline(values) {
      if (values) {
        baseline = {
          subject: String(values.subject || ''),
          html: String(values.html || ''),
          text: String(values.text || ''),
        };
      } else {
        setBaselineToCurrent();
      }
      updateUnsavedBadge();
    }

    function restoreBaseline() {
      if (!baseline) setBaselineToCurrent();
      if (!baseline) return;
      setValues({ subject: baseline.subject, html: baseline.html, text: baseline.text });
      updateUnsavedBadge();
    }

    function getCsrfToken() {
      var el = document.querySelector('input[name=csrfmiddlewaretoken]');
      return el ? String(el.value || '') : '';
    }

    function getTemplateSelectEl() {
      return q(container, 'select[name="email_template_id"]');
    }

    function getTemplateId() {
      var el = getTemplateSelectEl();
      return el ? String(el.value || '').trim() : '';
    }

    function setTemplateId(value) {
      var el = getTemplateSelectEl();
      if (!el) return;
      el.value = value == null ? '' : String(value);
    }

    async function loadTemplateJson(templateId) {
      templateId = String(templateId || '').trim();
      if (!templateId) return;

      try {
        var url = '/email-tools/templates/' + encodeURIComponent(templateId) + '/json/';
        var resp = await window.fetch(url, { headers: { 'Accept': 'application/json' } });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        var payload = await resp.json();

        setValues({
          subject: payload.subject,
          html: payload.html_content,
          text: payload.text_content,
        });
        markBaseline();
        setRestoreEnabled(true);
        dispatch('templated-email-compose:template-loaded', { instance: api, id: templateId, payload: payload });
      } catch (_e) {
        try { window.alert('Failed to load template.'); } catch (__e) { /* noop */ }
      }
    }

    function getModalEl(kind) {
      var wrapper = q(container, '[data-compose-modal="' + String(kind) + '"]');
      if (!wrapper) return null;
      return wrapper.querySelector('.modal');
    }

    function showModal(modalEl) {
      var jq = window.jQuery;
      if (jq && jq.fn && typeof jq.fn.modal === 'function') {
        jq(modalEl).modal('show');
        return true;
      }
      return false;
    }

    function hideModal(modalEl) {
      var jq = window.jQuery;
      if (jq && jq.fn && typeof jq.fn.modal === 'function') {
        jq(modalEl).modal('hide');
      }
    }

    function bindSaveModalsIfPresent() {
      var saveBtn = q(container, '[data-compose-action="save"]');
      if (saveBtn) {
        saveBtn.addEventListener('click', function (e) {
          e.preventDefault();

          var saveModalEl = getModalEl('save');
          if (saveModalEl && showModal(saveModalEl)) return;

          dispatch('templated-email-compose:save-confirmed', { instance: api });
        });
      }

      var saveAsBtn = q(container, '[data-compose-action="save-as"]');
      if (saveAsBtn) {
        saveAsBtn.addEventListener('click', function (e) {
          e.preventDefault();

          var saveAsModalEl = getModalEl('save-as');
          if (saveAsModalEl) {
            var nameEl = saveAsModalEl.querySelector('input[name="name"]');
            if (nameEl) nameEl.value = '';

            if (showModal(saveAsModalEl)) {
              var jq = window.jQuery;
              if (jq && jq.fn) {
                try {
                  jq(saveAsModalEl).one('shown.bs.modal', function () {
                    var el = saveAsModalEl.querySelector('input[name="name"]');
                    if (el && el.focus) el.focus();
                  });
                } catch (_e) {
                  // Ignore.
                }
              }
              return;
            }
          }

          var name = '';
          try {
            name = String(window.prompt('New template name:') || '').trim();
          } catch (_e) {
            name = '';
          }
          if (name) dispatch('templated-email-compose:save-as-confirmed', { instance: api, name: name });
        });
      }

      var saveModal = getModalEl('save');
      if (saveModal) {
        var formEl = saveModal.querySelector('form');
        if (formEl) {
          formEl.addEventListener('submit', function (e) {
            e.preventDefault();
            hideModal(saveModal);
            dispatch('templated-email-compose:save-confirmed', { instance: api });
          });
        }
      }

      var saveAsModal = getModalEl('save-as');
      if (saveAsModal) {
        var formEl2 = saveAsModal.querySelector('form');
        if (formEl2) {
          formEl2.addEventListener('submit', function (e) {
            e.preventDefault();
            var nameEl2 = saveAsModal.querySelector('input[name="name"]');
            var name2 = nameEl2 ? String(nameEl2.value || '').trim() : '';
            if (!name2) return;
            hideModal(saveAsModal);
            dispatch('templated-email-compose:save-as-confirmed', { instance: api, name: name2 });
          });
        }
      }
    }

    var api = {
      container: container,
      getField: getField,
      setField: setField,
      getValues: getCurrentValues,
      setValues: setValues,
      markBaseline: markBaseline,
      restoreBaseline: restoreBaseline,
      setRestoreEnabled: setRestoreEnabled,
      loadTemplateJson: loadTemplateJson,
      getCsrfToken: getCsrfToken,
      getTemplateSelectEl: getTemplateSelectEl,
      getTemplateId: getTemplateId,
      setTemplateId: setTemplateId,
    };

    setBaselineToCurrent();
    initCodeMirror();
    bindSaveModalsIfPresent();

    var subject = q(container, '[name="subject"]');
    if (subject) {
      subject.addEventListener('input', function () {
        updateUnsavedBadge();
        dispatch('templated-email-compose:content-updated', { instance: api });
      });
      subject.addEventListener('change', function () {
        updateUnsavedBadge();
        dispatch('templated-email-compose:content-updated', { instance: api });
      });
    }

    // If CodeMirror fails to load/initialize (or is intentionally not used),
    // keep previews reactive via textarea input events.
    var htmlTa = q(container, 'textarea[name="html_content"]');
    if (htmlTa) {
      htmlTa.addEventListener('input', function () {
        updateUnsavedBadge();
        dispatch('templated-email-compose:content-updated', { instance: api });
      });
      htmlTa.addEventListener('change', function () {
        updateUnsavedBadge();
        dispatch('templated-email-compose:content-updated', { instance: api });
      });
    }

    var textTa = q(container, 'textarea[name="text_content"]');
    if (textTa) {
      textTa.addEventListener('input', function () {
        updateUnsavedBadge();
        dispatch('templated-email-compose:content-updated', { instance: api });
      });
      textTa.addEventListener('change', function () {
        updateUnsavedBadge();
        dispatch('templated-email-compose:content-updated', { instance: api });
      });
    }

    var templateSelect = q(container, 'select[name="email_template_id"]');
    if (templateSelect) {
      templateSelect.addEventListener('change', function () {
        var id = String(templateSelect.value || '').trim();
        if (!id) return;
        loadTemplateJson(id);
      });
    }

    var restoreBtn = q(container, '[data-compose-action="restore"]');
    if (restoreBtn) {
      restoreBtn.addEventListener('click', function (e) {
        e.preventDefault();
        restoreBaseline();
        dispatch('templated-email-compose:content-updated', { instance: api });
      });
    }

    updateUnsavedBadge();
    return api;
  }

  function initAll() {
    var containers = document.querySelectorAll('[data-templated-email-compose]');
    if (!containers || !containers.length) return;

    for (var i = 0; i < containers.length; i++) {
      var instance = initCompose(containers[i]);
      if (!instance) continue;

      window.TemplatedEmailComposeRegistry.instances.push(instance);

      // Backward-compatible default instance for existing page JS.
      if (!window.TemplatedEmailCompose) {
        window.TemplatedEmailCompose = instance;
      }
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
})(window, document);

(function (window, document) {
  'use strict';

  // Live preview helper.
  if (window.TemplatedEmailComposePreview) return;

  var timersByContainer = new WeakMap();

  function getComposeFromEvent(e) {
    if (e && e.detail && e.detail.instance) return e.detail.instance;
    return window.TemplatedEmailCompose || null;
  }

  function getPreviewBox(compose, kind) {
    if (!compose || !compose.container) return null;
    return compose.container.querySelector('[data-compose-preview="' + String(kind) + '"]');
  }

  function supportsSrcdoc(iframe) {
    // Some older browsers don't support srcdoc; keep a fallback so preview
    // updates never break the rest of the widget.
    try {
      return iframe && ('srcdoc' in iframe);
    } catch (_e) {
      return false;
    }
  }

  function wrapHtmlForIframe(html) {
    var body = String(html || '');

    // If the preview already includes a full document, keep it as-is.
    // This avoids nesting <html>/<body> tags in a way that can render oddly.
    if (/<\s*html[\s>]/i.test(body) || /<\s*body[\s>]/i.test(body)) {
      return body;
    }

    return (
      '<!doctype html>' +
      '<html>' +
      '<head>' +
      '<meta charset="utf-8">' +
      // Keep links from navigating inside the iframe.
      '<base target="_blank">' +
      '<meta name="viewport" content="width=device-width, initial-scale=1">' +
      '<style>html,body{margin:0;padding:0;background:#fff;}body{padding:8px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.35;}</style>' +
      '</head>' +
      '<body>' + body + '</body>' +
      '</html>'
    );
  }

  function wrapTextForIframe(text) {
    var body = String(text || '');
    return (
      '<!doctype html>' +
      '<html>' +
      '<head>' +
      '<meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width, initial-scale=1">' +
      '<style>html,body{margin:0;padding:0;background:#fff;}body{padding:8px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.35;}pre{margin:0;white-space:pre-wrap;font-family:inherit;}</style>' +
      '</head>' +
      '<body><pre>' + body + '</pre></body>' +
      '</html>'
    );
  }

  function ensurePreviewIframe(box) {
    if (!box) return null;

    var existing = null;
    try {
      existing = box.querySelector('iframe[data-compose-preview-iframe="1"]');
    } catch (_e0) {
      existing = null;
    }
    if (existing) return existing;

    var iframe = null;
    try {
      iframe = document.createElement('iframe');
    } catch (_e1) {
      iframe = null;
    }
    if (!iframe) return null;

    iframe.setAttribute('data-compose-preview-iframe', '1');
    iframe.setAttribute('title', 'Rendered HTML preview');
    // Sandbox to prevent preview HTML from affecting the parent page.
    // Allow popups so links can be followed in a new tab.
    iframe.setAttribute('sandbox', 'allow-popups');
    iframe.setAttribute('referrerpolicy', 'no-referrer');
    iframe.style.display = 'block';
    iframe.style.width = '100%';
    iframe.style.height = '400px';
    iframe.style.border = '0';
    iframe.style.background = '#fff';

    // Clear any legacy preview HTML. This is important because malformed HTML
    // (or <style> tags) can leak into the surrounding page.
    try {
      box.textContent = '';
    } catch (_e2) {
      // Ignore.
    }
    box.appendChild(iframe);
    return iframe;
  }

  function setPreviewHtml(compose, html) {
    var box = getPreviewBox(compose, 'html');
    if (!box) return;

    var iframe = ensurePreviewIframe(box);
    if (!iframe) {
      // Last-resort fallback: preserve existing behavior.
      box.innerHTML = html || '<span class="text-muted">No preview yet.</span>';
      return;
    }

    var content = html || '<span class="text-muted">No preview yet.</span>';
    var doc = wrapHtmlForIframe(content);

    if (supportsSrcdoc(iframe)) {
      iframe.srcdoc = doc;
      return;
    }

    // Fallback for browsers without srcdoc.
    try {
      var w = iframe.contentWindow;
      if (!w || !w.document) throw new Error('no document');
      w.document.open();
      w.document.write(doc);
      w.document.close();
    } catch (_e) {
      box.innerHTML = content;
    }
  }

  function setPreviewText(compose, text) {
    var box = getPreviewBox(compose, 'text');
    if (!box) return;

    var iframe = ensurePreviewIframe(box);
    if (!iframe) {
      box.textContent = text || 'No preview yet.';
      return;
    }

    var content = text || 'No preview yet.';
    var doc = wrapTextForIframe(content);

    if (supportsSrcdoc(iframe)) {
      iframe.srcdoc = doc;
      return;
    }

    try {
      var w = iframe.contentWindow;
      if (!w || !w.document) throw new Error('no document');
      w.document.open();
      w.document.write(doc);
      w.document.close();
    } catch (_e) {
      box.textContent = content;
    }
  }

  function getPreviewUrl(compose) {
    if (!compose || !compose.container) return '';
    return String(compose.container.getAttribute('data-compose-preview-url') || '').trim();
  }

  async function refreshPreview(compose) {
    if (!compose) return;

    var url = getPreviewUrl(compose);
    if (!url) return;

    var data = new window.FormData();
    data.append('subject', compose.getField('subject'));
    data.append('html_content', compose.getField('html_content'));
    data.append('text_content', compose.getField('text_content'));

    // Election edit page: include the current (possibly unsaved) election details so
    // the server-side preview can reflect draft values without requiring a save.
    // Other preview endpoints ignore extra fields safely.
    var extraFields = [
      ['name', 'id_name'],
      ['description', 'id_description'],
      ['url', 'id_url'],
      ['start_datetime', 'id_start_datetime'],
      ['end_datetime', 'id_end_datetime'],
      ['number_of_seats', 'id_number_of_seats'],
      ['eligible_group_cn', 'id_eligible_group_cn'],
    ];
    for (var i = 0; i < extraFields.length; i++) {
      var pair = extraFields[i];
      var key = pair[0];
      var elId = pair[1];
      var el = null;
      try {
        el = document.getElementById(elId);
      } catch (_e0) {
        el = null;
      }
      if (!el) continue;
      var val = '';
      try {
        val = String(el.value || '');
      } catch (_e1) {
        val = '';
      }
      data.append(key, val);
    }

    try {
      var resp = await window.fetch(url, {
        method: 'POST',
        headers: { 'X-CSRFToken': compose.getCsrfToken(), 'Accept': 'application/json' },
        body: data
      });

      var payload = null;
      try {
        payload = await resp.json();
      } catch (_e2) {
        payload = null;
      }

      if (!resp.ok) {
        if (payload && payload.error) {
          setPreviewHtml(compose, '<span class="text-muted">Preview unavailable.</span>');
          setPreviewText(compose, payload.error);
        }
        return;
      }

      if (!payload) return;
      setPreviewHtml(compose, payload.html);
      setPreviewText(compose, payload.text);
    } catch (_e) {
      // Ignore transient failures.
    }
  }

  function schedulePreviewRefresh(compose, delayMs) {
    if (!compose || !compose.container) return;

    var existing = timersByContainer.get(compose.container);
    if (existing) window.clearTimeout(existing);

    var ms = typeof delayMs === 'number' ? delayMs : 50;
    var id = window.setTimeout(function () {
      refreshPreview(compose);
    }, ms);

    timersByContainer.set(compose.container, id);
  }

  function initGlobalListeners() {
    if (initGlobalListeners._done) return;
    initGlobalListeners._done = true;

    document.addEventListener('templated-email-compose:content-updated', function (e) {
      schedulePreviewRefresh(getComposeFromEvent(e), 50);
    });

    document.addEventListener('templated-email-compose:template-loaded', function (e) {
      schedulePreviewRefresh(getComposeFromEvent(e), 0);
    });

    // Initial refresh for already-initialized compose widgets.
    window.setTimeout(function () {
      var reg = window.TemplatedEmailComposeRegistry;
      if (!reg || typeof reg.getAll !== 'function') return;

      var instances = reg.getAll();
      for (var i = 0; i < instances.length; i++) {
        schedulePreviewRefresh(instances[i], 0);
      }
    }, 0);
  }

  window.TemplatedEmailComposePreview = {
    getComposeFromEvent: getComposeFromEvent,
    schedulePreviewRefresh: schedulePreviewRefresh,
    refreshPreview: refreshPreview,
    setPreviewHtml: setPreviewHtml,
    setPreviewText: setPreviewText,
  };

  initGlobalListeners();
})(window, document);
