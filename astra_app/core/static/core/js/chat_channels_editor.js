(function () {
  function onReady(fn) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', fn);
    } else {
      fn();
    }
  }

  function parseStoredLine(line, defaults) {
    const raw = String(line || '').trim();
    if (!raw) return null;

    try {

    const lower = raw.toLowerCase();
    const startsWithScheme =
      lower.startsWith('mattermost:') ||
      lower.startsWith('irc:') ||
      lower.startsWith('ircs:') ||
      lower.startsWith('matrix:');

    let scheme = 'irc';
    let value = raw;

    if (startsWithScheme) {
      const parts = raw.split(':', 2);
      scheme = (parts[0] || 'irc').toLowerCase();
      if (scheme === 'ircs') scheme = 'irc';

      const rest = raw.slice((parts[0] + ':').length);
      if (rest.startsWith('//')) {
        // Note: URLs containing #channel end up in the fragment.
        const parsed = new URL(rest.startsWith('//') ? 'scheme:' + rest : rest);
        const server = parsed.host || '';
        const path = String(parsed.pathname || '').replace(/^\//, '');
        const fragment = String(parsed.hash || '').replace(/^#/, '');

        if (scheme === 'mattermost') {
          const pathParts = path.split('/').filter(Boolean);
          let team = '';
          let channel = '';
          if (pathParts.length >= 3 && pathParts[1] === 'channels') {
            team = pathParts[0];
            channel = pathParts[2];
          } else if (pathParts.length >= 2 && pathParts[0] === 'channels') {
            channel = pathParts[1];
          }

          const defaultServer = defaults.mattermostServer;
          const defaultTeam = defaults.mattermostTeam;
          const effectiveServer = server || defaultServer;
          const effectiveTeam = team || (effectiveServer === defaultServer ? defaultTeam : '');

          const base = `~${String(channel || '').replace(/^~/, '')}`;
          if (!effectiveServer || !effectiveTeam) return { scheme, value: base };

          const isDefault = (effectiveServer === defaultServer) && (effectiveTeam === defaultTeam);
          const display = isDefault ? base : `${base}:${effectiveServer}:${effectiveTeam}`;
          return { scheme, value: display };
        }

        if (scheme === 'matrix') {
          const defaultServer = defaults.matrix;
          const ch = fragment ? `#${fragment}` : (path ? (path.startsWith('#') ? path : `#${path}`) : '');
          const base = ch;
          const display = (server && defaultServer && server !== defaultServer) ? `${base}:${server}` : base;
          return { scheme, value: display };
        }

        // IRC
        const defaultServer = defaults.irc;
        const ch = fragment ? `#${fragment}` : (path ? (path.startsWith('#') ? path : `#${path}`) : '');
        const base = ch;
        const display = (server && defaultServer && server !== defaultServer) ? `${base}:${server}` : base;
        return { scheme, value: display };
      }

      if (rest.startsWith('/')) {
        // Stored default forms: irc:/#chan, matrix:/#chan, mattermost:/channels/chan
        value = rest.slice(1);
        if (scheme === 'mattermost') {
          const parts2 = String(value || '').split('/').filter(Boolean);
          const channel = (parts2.length >= 2 && parts2[0] === 'channels') ? parts2[1] : '';
          return { scheme, value: `~${channel}` };
        }
        if (value.startsWith('#')) {
          return { scheme, value };
        }
        return { scheme, value: value ? `#${value}` : '' };
      }

      return { scheme, value: String(value || '').trim() };
    }

    // Plain forms.
      if (raw.startsWith('~')) return { scheme: 'mattermost', value: raw };
      if (raw.startsWith('#')) return { scheme: 'irc', value: raw };

      return { scheme: 'irc', value: raw };
    } catch (err) {
      return null;
    }
  }

  function readChatNetworksDefaults() {
    const fallback = {
      mattermostServer: 'chat.almalinux.org',
      mattermostTeam: 'almalinux',
      irc: 'irc.libera.chat',
      matrix: 'matrix.org'
    };

    const script = document.getElementById('admin-chat-networks');
    if (!script) return fallback;

    try {
      const data = JSON.parse(script.textContent || '{}');
      const mattermost = (data && data.mattermost) || {};
      const irc = (data && data.irc) || {};
      const matrix = (data && data.matrix) || {};
      return {
        mattermostServer: mattermost.default_server || fallback.mattermostServer,
        mattermostTeam: mattermost.default_team || fallback.mattermostTeam,
        irc: irc.default_server || fallback.irc,
        matrix: matrix.default_server || fallback.matrix
      };
    } catch (err) {
      return fallback;
    }
  }

  function toStoredLine(scheme, value, defaults) {
    const raw = String(value || '').trim();
    if (!raw) return '';

    const normalizedScheme = (scheme === 'mattermost') ? 'mattermost' : (scheme === 'matrix') ? 'matrix' : 'irc';

    const lower = raw.toLowerCase();
    if (lower.startsWith('mattermost:') || lower.startsWith('irc:') || lower.startsWith('ircs:') || lower.startsWith('matrix:')) {
      return raw;
    }

    if (normalizedScheme === 'mattermost') {
      const defaultServer = defaults.mattermostServer;
      const defaultTeam = defaults.mattermostTeam;

      const cleaned = raw.replace(/^~/, '').trim();
      const colonCount = (cleaned.match(/:/g) || []).length;

      if (colonCount >= 2) {
        const parts = cleaned.split(':').filter(Boolean);
        const channel = parts[0] || '';
        const team = parts.length >= 2 ? parts[parts.length - 1] : '';
        const server = parts.length >= 3 ? parts.slice(1, -1).join(':') : '';

        if (channel && server && team) {
          const isDefault = (server === defaultServer) && (team === defaultTeam);
          if (isDefault) return `mattermost:/channels/${channel}`;
          return `mattermost://${server}/${team}/channels/${channel}`;
        }
      }

      // Default form.
      if (cleaned) return `mattermost:/channels/${cleaned}`;
      return '';
    }

    if (normalizedScheme === 'matrix') {
      const defaultServer = defaults.matrix;
      if (raw.includes(':')) {
        const idx = raw.lastIndexOf(':');
        const ch = raw.slice(0, idx).trim();
        const server = raw.slice(idx + 1).trim();
        if (ch && server) {
          if (server === defaultServer) return `matrix:/${ch}`;
          return `matrix://${server}/${ch}`;
        }
      }
      return `matrix:/${raw}`;
    }

    // IRC
    const defaultServer = defaults.irc;
    if (raw.includes(':')) {
      const idx = raw.lastIndexOf(':');
      const ch = raw.slice(0, idx).trim();
      const server = raw.slice(idx + 1).trim();
      if (ch && server) {
        if (server === defaultServer) return `irc:/${ch}`;
        return `irc://${server}/${ch}`;
      }
    }
    return `irc:/${raw}`;
  }

  function buildRow(rowData) {
    const tr = document.createElement('tr');
    tr.className = 'chat-channels-row';

    tr.innerHTML = `
      <td style="width: 160px;">
        <select class="custom-select custom-select-sm chat-channels-scheme" aria-label="Chat protocol">
          <option value="mattermost">Mattermost</option>
          <option value="irc">IRC</option>
          <option value="matrix">Matrix</option>
        </select>
      </td>
      <td>
        <input type="text" class="form-control form-control-sm chat-channels-value" placeholder="#channel" />
      </td>
      <td style="width: 44px;" class="text-center">
        <button type="button" class="btn btn-sm btn-outline-secondary chat-channels-remove" aria-label="Remove">×</button>
      </td>
    `;

    const schemeEl = tr.querySelector('.chat-channels-scheme');
    const valueEl = tr.querySelector('.chat-channels-value');

    function enhanceSelect(el) {
      const jq = (window.django && window.django.jQuery) ? window.django.jQuery : window.jQuery;
      if (!jq || !jq.fn || !jq.fn.select2) return;
      const wrapped = jq(el);
      if (wrapped.hasClass('select2-hidden-accessible')) return;
      wrapped.select2({ width: 'element' });
    }

    function setPlaceholder() {
      valueEl.placeholder = (schemeEl.value === 'mattermost') ? '~channel' : '#channel';
    }

    schemeEl.value = rowData.scheme;
    valueEl.value = rowData.value;
    setPlaceholder();

    enhanceSelect(schemeEl);

    schemeEl.addEventListener('change', setPlaceholder);
    return tr;
  }

  function initChatChannelsEditor(root) {
    const textareaId = root.getAttribute('data-textarea-id');
    const fallbackId = root.getAttribute('data-fallback-id');

    const textarea = textareaId ? document.getElementById(textareaId) : null;
    const fallback = fallbackId ? document.getElementById(fallbackId) : null;
    const tableBody = root.querySelector('tbody');
    const addBtn = root.querySelector('.js-chat-channels-add');

    const form = textarea && textarea.form;
    if (!textarea || !fallback || !tableBody || !addBtn || !form) return;

    const defaults = {
      mattermostServer: root.getAttribute('data-mattermost-default-server') || 'chat.almalinux.org',
      mattermostTeam: root.getAttribute('data-mattermost-default-team') || 'almalinux',
      irc: root.getAttribute('data-irc-default-server') || 'irc.libera.chat',
      matrix: root.getAttribute('data-matrix-default-server') || 'matrix.org'
    };

    function syncToTextarea() {
      const rows = Array.from(tableBody.querySelectorAll('tr.chat-channels-row'));
      const lines = [];
      for (const row of rows) {
        const scheme = (row.querySelector('.chat-channels-scheme') || {}).value || 'irc';
        const value = (row.querySelector('.chat-channels-value') || {}).value || '';
        const stored = toStoredLine(scheme, value, defaults);
        if (stored) lines.push(stored);
      }
      textarea.value = lines.join('\n');
    }

    function addRow(rowData) {
      const tr = buildRow(rowData);

      const schemeEl = tr.querySelector('.chat-channels-scheme');
      const valueEl = tr.querySelector('.chat-channels-value');
      const removeBtn = tr.querySelector('.chat-channels-remove');

      schemeEl.addEventListener('change', syncToTextarea);
      valueEl.addEventListener('input', syncToTextarea);
      removeBtn.addEventListener('click', function () {
        tr.remove();
        syncToTextarea();
      });

      tableBody.appendChild(tr);
      syncToTextarea();
    }

    const existing = String(textarea.value || '')
      .replaceAll('\r', '')
      .split('\n')
      .map(function (l) { return parseStoredLine(l, defaults); })
      .filter(Boolean);

    if (existing.length === 0) {
      existing.push({ scheme: 'mattermost', value: '' });
    }

    for (const rowData of existing) {
      addRow(rowData);
    }

    addBtn.addEventListener('click', function () {
      addRow({ scheme: 'mattermost', value: '' });
      const lastInput = tableBody.querySelector('tr.chat-channels-row:last-child .chat-channels-value');
      if (lastInput) lastInput.focus();
    });

    form.addEventListener('submit', function () {
      syncToTextarea();
    });

    const hasErrors = !!fallback.querySelector('.errorlist, .invalid-feedback');
    if (!hasErrors) {
      fallback.classList.add('d-none');
    }
    root.classList.remove('d-none');
  }

  function initChatChannelsEditorForTextarea(textarea, defaults) {
    if (!textarea || !textarea.id) return;
    if (textarea.getAttribute('data-chat-channels-bound') === '1') return;

    const fallbackId = `chat-channels-fallback-${textarea.id}`;
    const existingFallback = document.getElementById(fallbackId);
    if (!existingFallback) {
      const wrapper = document.createElement('div');
      wrapper.id = fallbackId;
      textarea.parentNode.insertBefore(wrapper, textarea);
      wrapper.appendChild(textarea);
    }

    const root = document.createElement('div');
    root.className = 'd-none js-chat-channels-editor';
    root.setAttribute('data-textarea-id', textarea.id);
    root.setAttribute('data-fallback-id', fallbackId);
    root.setAttribute('data-mattermost-default-server', defaults.mattermostServer);
    root.setAttribute('data-mattermost-default-team', defaults.mattermostTeam);
    root.setAttribute('data-irc-default-server', defaults.irc);
    root.setAttribute('data-matrix-default-server', defaults.matrix);

    root.innerHTML = `
      <div class="table-responsive">
        <table class="table table-sm table-bordered align-middle mb-2">
          <tbody></tbody>
        </table>
      </div>
      <button type="button" class="btn btn-sm btn-outline-secondary js-chat-channels-add">Add channel</button>
    `;

    const fallback = document.getElementById(fallbackId);
    fallback.parentNode.insertBefore(root, fallback.nextSibling);
    initChatChannelsEditor(root);
    textarea.setAttribute('data-chat-channels-bound', '1');
  }

  onReady(function () {
    const roots = document.querySelectorAll('.js-chat-channels-editor');
    for (const root of roots) {
      initChatChannelsEditor(root);
    }

    const defaults = readChatNetworksDefaults();
    const adminTextareas = document.querySelectorAll(
      'textarea[data-chat-channels-editor="1"], textarea[name="fas_irc_channels"], textarea#id_fas_irc_channels'
    );
    for (const textarea of adminTextareas) {
      initChatChannelsEditorForTextarea(textarea, defaults);
    }
  });
})();
