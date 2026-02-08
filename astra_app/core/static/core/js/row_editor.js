/**
 * Shared row-editor widget for list-type textarea fields.
 *
 * Renders the list as an editable table of rows, syncing changes back to a
 * hidden <textarea>. Both the profile URL editor and the keys editor use this.
 *
 * Usage:
 *   setupRowEditor({
 *     textareaId:       'id_fasWebsiteUrl',
 *     widgetId:         'website-urls-widget',
 *     fallbackId:       'website-urls-fallback',
 *     tableBodySelector:'#website-urls-table tbody',
 *     addBtnId:         'website-urls-add',
 *     rowClass:         'website-urls-row',
 *     kind:             'url',       // 'url' | 'text' | 'textarea'
 *     placeholder:      'https://…', // optional
 *     inputClass:       '',          // optional extra CSS class on input
 *     splitComma:       true,        // also split on commas (URL lists)
 *   });
 */

/* exported setupRowEditor */
// eslint-disable-next-line no-unused-vars
function setupRowEditor(opts) {
  'use strict';

  var textarea = document.getElementById(opts.textareaId);
  var widget = document.getElementById(opts.widgetId);
  var fallback = document.getElementById(opts.fallbackId);
  var tableBody = document.querySelector(opts.tableBodySelector);
  var addBtn = document.getElementById(opts.addBtnId);

  if (!textarea || !widget || !fallback || !tableBody || !addBtn || !textarea.form) return;

  var kind = opts.kind || 'text';
  var placeholder = opts.placeholder || '';
  var inputClass = opts.inputClass || '';
  var splitComma = !!opts.splitComma;

  function parseExisting(value) {
    var lines = String(value || '').replaceAll('\r', '').split('\n');
    if (!splitComma) {
      return lines.map(function (l) { return String(l || '').trim(); }).filter(Boolean);
    }
    return lines.flatMap(function (line) {
      return String(line || '').split(',').map(function (p) { return String(p || '').trim(); }).filter(Boolean);
    });
  }

  function buildRow(initialValue) {
    var tr = document.createElement('tr');
    tr.className = opts.rowClass;

    var inputHtml;
    if (kind === 'textarea') {
      inputHtml = '<textarea class="form-control form-control-sm text-monospace' +
        (inputClass ? ' ' + inputClass : '') +
        '" rows="2" placeholder="' + placeholder + '"></textarea>';
    } else {
      inputHtml = '<input type="' + (kind === 'url' ? 'url' : 'text') +
        '" class="form-control form-control-sm text-monospace' +
        (inputClass ? ' ' + inputClass : '') +
        '" placeholder="' + placeholder + '" />';
    }

    tr.innerHTML =
      '<td>' + inputHtml + '</td>' +
      '<td style="width: 44px;" class="text-center">' +
      '<button type="button" class="btn btn-sm btn-outline-secondary" aria-label="Remove" title="Remove this entry">\u00d7</button>' +
      '</td>';

    var valueEl = tr.querySelector('input,textarea');
    var removeBtn = tr.querySelector('button');

    valueEl.value = initialValue || '';
    valueEl.addEventListener('input', syncToTextarea);
    removeBtn.addEventListener('click', function () {
      tr.remove();
      syncToTextarea();
    });

    return tr;
  }

  function syncToTextarea() {
    var rows = Array.from(tableBody.querySelectorAll('tr.' + opts.rowClass));
    var lines = [];
    for (var i = 0; i < rows.length; i++) {
      var valueEl = rows[i].querySelector('input,textarea');
      var raw = (valueEl && valueEl.value) ? String(valueEl.value) : '';
      var v = raw.replaceAll('\r', '').trim();
      if (v) lines.push(v);
    }
    textarea.value = lines.join('\n');
  }

  function addRow(value) {
    var tr = buildRow(value);
    tableBody.appendChild(tr);
    syncToTextarea();
    return tr;
  }

  // Initialize rows from textarea content.
  var existing = parseExisting(textarea.value);
  if (existing.length === 0) {
    addRow('');
  } else {
    for (var i = 0; i < existing.length; i++) addRow(existing[i]);
  }

  addBtn.addEventListener('click', function () {
    var tr = addRow('');
    var input = tr.querySelector('input,textarea');
    if (input) input.focus();
  });

  textarea.form.addEventListener('submit', function () {
    syncToTextarea();
  });

  // Keep the fallback visible if there are server-side validation errors.
  var hasErrors = !!fallback.querySelector('.errorlist, .text-danger .errorlist, .invalid-feedback');
  if (!hasErrors) {
    fallback.classList.add('d-none');
  }
  widget.classList.remove('d-none');
}
