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

    const lower = raw.toLowerCase();
    const startsWithScheme = lower.startsWith('mattermost:') || lower.startsWith('irc:') || lower.startsWith('matrix:');

    let scheme = 'mattermost';
    let nick = raw;
    let server = '';
    let team = '';

    if (startsWithScheme) {
      const parts = raw.split(':', 2);
      scheme = (parts[0] || 'mattermost').toLowerCase();

      const rest = raw.slice((scheme + ':').length);
      if (rest.startsWith('//')) {
        const withoutSlashes = rest.slice(2);
        const firstSlash = withoutSlashes.indexOf('/');
        if (firstSlash >= 0) {
          server = withoutSlashes.slice(0, firstSlash);
          nick = withoutSlashes.slice(firstSlash + 1);
        } else {
          // For stored chat nicknames, scheme URLs like mattermost://<nick> are valid and
          // common (defaults). Treat the authority component as the nick when there's no
          // path component.
          nick = withoutSlashes;
          server = '';
        }
      } else if (rest.startsWith('/')) {
        nick = rest.slice(1);
      } else {
        nick = rest;
      }
    } else if (raw.startsWith('@') && raw.includes(':')) {
      scheme = 'matrix';
      const withoutAt = raw.slice(1);
      const idx = withoutAt.lastIndexOf(':');
      nick = withoutAt.slice(0, idx);
      server = withoutAt.slice(idx + 1);
    } else if (raw.includes(':')) {
      scheme = 'irc';
      const idx = raw.lastIndexOf(':');
      nick = raw.slice(0, idx);
      server = raw.slice(idx + 1);
    } else if (raw.includes('@')) {
      scheme = 'irc';
      const idx = raw.lastIndexOf('@');
      nick = raw.slice(0, idx);
      server = raw.slice(idx + 1);
    }

    nick = String(nick || '').trim().replace(/^@/, '');
    server = String(server || '').trim();
    if (!nick && !server) return null;

    if (scheme === 'mattermost') {
      const defaultServer = defaults.mattermostServer;
      const defaultTeam = defaults.mattermostTeam;

      // Stored Mattermost values use mattermost://server/team/nick.
      // Legacy values may be mattermost://server/nick.
      const pathParts = String(nick || '').split('/').filter(Boolean);
      if (pathParts.length >= 2) {
        team = pathParts[0];
        if (pathParts.length >= 3 && pathParts[1] === 'messages' && String(pathParts[2] || '').startsWith('@')) {
          nick = String(pathParts[2]).replace(/^@/, '');
        } else {
          nick = pathParts[1];
        }
      }

      const base = `@${String(nick || '').replace(/^@/, '')}`;
      const effectiveServer = server || defaultServer;
      const effectiveTeam = team || (effectiveServer === defaultServer ? defaultTeam : '');

      if (!effectiveServer) return { scheme, value: base };
      if (!effectiveTeam) return { scheme, value: base };

      const isDefault = (effectiveServer === defaultServer) && (effectiveTeam === defaultTeam);
      const value = isDefault ? base : `${base}:${effectiveServer}:${effectiveTeam}`;
      return { scheme, value };
    }

    if (scheme === 'matrix') {
      const defaultServer = defaults.matrix;
      const base = `@${nick}`;
      const value = (server && defaultServer && server !== defaultServer) ? `${base}:${server}` : base;
      return { scheme, value };
    }

    // IRC
    const defaultServer = defaults.irc;
    const value = (server && defaultServer && server !== defaultServer) ? (nick ? `${nick}:${server}` : server) : nick;
    return { scheme, value };
  }

  function toStoredLine(scheme, value, defaults) {
    const raw = String(value || '').trim();
    if (!raw) return '';

    const normalizedScheme = (scheme === 'mattermost') ? 'mattermost' : (scheme === 'matrix') ? 'matrix' : 'irc';
    const defaultServer =
      (normalizedScheme === 'mattermost') ? defaults.mattermostServer :
      (normalizedScheme === 'matrix') ? defaults.matrix :
      defaults.irc;

    const lower = raw.toLowerCase();
    if (lower.startsWith('mattermost:') || lower.startsWith('irc:') || lower.startsWith('matrix:')) return raw;

    if (normalizedScheme === 'matrix') {
      if (raw.startsWith('@') && raw.includes(':')) {
        const withoutAt = raw.slice(1);
        const idx = withoutAt.lastIndexOf(':');
        const nick = withoutAt.slice(0, idx).trim();
        const server = withoutAt.slice(idx + 1).trim();
        if (nick && server) return `matrix://${server}/${nick}`;
      }

      if (raw.includes(':')) {
        const idx = raw.lastIndexOf(':');
        const nick = raw.slice(0, idx).trim().replace(/^@/, '');
        const server = raw.slice(idx + 1).trim();
        if (nick && server) {
          if (server === defaultServer) return `matrix:/${nick}`;
          return `matrix://${server}/${nick}`;
        }
      }

      return `matrix:/${raw.replace(/^@/, '')}`;
    }

    if (normalizedScheme === 'mattermost') {
      const defaultTeam = defaults.mattermostTeam;
      const cleaned = raw.replace(/^@/, '').trim();
      const colonCount = (cleaned.match(/:/g) || []).length;

      if (colonCount >= 2) {
        const parts = cleaned.split(':').filter(Boolean);
        const nick = parts[0] || '';
        const team = parts.length >= 2 ? parts[parts.length - 1] : '';
        const server = parts.length >= 3 ? parts.slice(1, -1).join(':') : '';

        if (nick && server && team) {
          const isDefault = (server === defaultServer) && (team === defaultTeam);
          if (isDefault) return `mattermost://${nick}`;
          return `mattermost://${server}/${team}/${nick}`;
        }
      }

      // If the user enters @nick:server (missing team), keep it as a literal
      // value and let server-side validation produce a clear error.
      return `mattermost://${cleaned}`;
    }

    // IRC
    if (raw.includes(':')) {
      const idx = raw.lastIndexOf(':');
      const nick = raw.slice(0, idx).trim();
      const server = raw.slice(idx + 1).trim();
      if (nick && server) {
        if (server === defaultServer) return `irc://${nick}`;
        return `irc://${server}/${nick}`;
      }
    }
    if (raw.includes('@')) {
      const idx = raw.lastIndexOf('@');
      const nick = raw.slice(0, idx).trim();
      const server = raw.slice(idx + 1).trim();
      if (nick && server) {
        if (server === defaultServer) return `irc://${nick}`;
        return `irc://${server}/${nick}`;
      }
    }
    return `irc://${raw}`;
  }

  function buildRow(rowData) {
    const tr = document.createElement('tr');
    tr.className = 'chat-nicks-row';

    tr.innerHTML = `
      <td style="width: 160px;">
        <select class="custom-select custom-select-sm chat-nicks-scheme" aria-label="Chat protocol">
          <option value="mattermost">Mattermost</option>
          <option value="irc">IRC</option>
          <option value="matrix">Matrix</option>
        </select>
      </td>
      <td>
        <input type="text" class="form-control form-control-sm chat-nicks-value" placeholder="username" />
      </td>
      <td style="width: 44px;" class="text-center">
        <button type="button" class="btn btn-sm btn-outline-secondary chat-nicks-remove" aria-label="Remove">×</button>
      </td>
    `;

    const schemeEl = tr.querySelector('.chat-nicks-scheme');
    const valueEl = tr.querySelector('.chat-nicks-value');

    function setPlaceholder() {
      valueEl.placeholder = (schemeEl.value === 'irc') ? 'username' : '@username';
    }

    schemeEl.value = rowData.scheme;
    valueEl.value = rowData.value;
    setPlaceholder();

    schemeEl.addEventListener('change', setPlaceholder);
    return tr;
  }

  function initChatNicknamesEditor(root) {
    const textareaId = root.getAttribute('data-textarea-id');
    const fallbackId = root.getAttribute('data-fallback-id');

    const textarea = textareaId ? document.getElementById(textareaId) : null;
    const fallback = fallbackId ? document.getElementById(fallbackId) : null;
    const tableBody = root.querySelector('tbody');
    const addBtn = root.querySelector('.js-chat-nicks-add');

    const form = textarea && textarea.form;
    if (!textarea || !fallback || !tableBody || !addBtn || !form) return;

    const defaults = {
      mattermostServer: root.getAttribute('data-mattermost-default-server') || 'chat.almalinux.org',
      mattermostTeam: root.getAttribute('data-mattermost-default-team') || 'almalinux',
      irc: root.getAttribute('data-irc-default-server') || 'irc.libera.chat',
      matrix: root.getAttribute('data-matrix-default-server') || 'matrix.org'
    };

    function syncToTextarea() {
      const rows = Array.from(tableBody.querySelectorAll('tr.chat-nicks-row'));
      const lines = [];
      for (const row of rows) {
        const scheme = (row.querySelector('.chat-nicks-scheme') || {}).value || 'irc';
        const value = (row.querySelector('.chat-nicks-value') || {}).value || '';
        const stored = toStoredLine(scheme, value, defaults);
        if (stored) lines.push(stored);
      }
      textarea.value = lines.join('\n');
    }

    function addRow(rowData) {
      const tr = buildRow(rowData);

      const schemeEl = tr.querySelector('.chat-nicks-scheme');
      const valueEl = tr.querySelector('.chat-nicks-value');
      const removeBtn = tr.querySelector('.chat-nicks-remove');

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
      const lastInput = tableBody.querySelector('tr.chat-nicks-row:last-child .chat-nicks-value');
      if (lastInput) lastInput.focus();
    });

    form.addEventListener('submit', function () {
      syncToTextarea();
    });

    // Progressive enhancement: hide textarea UI once JS is active.
    // Keep it visible when server-side validation errors are present.
    const hasErrors = !!fallback.querySelector('.errorlist, .invalid-feedback');
    if (!hasErrors) {
      fallback.classList.add('d-none');
    }
    root.classList.remove('d-none');
  }

  onReady(function () {
    const roots = document.querySelectorAll('.js-chat-nicks-editor');
    for (const root of roots) {
      initChatNicknamesEditor(root);
    }
  });
})();
