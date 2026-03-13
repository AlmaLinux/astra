(function () {
  function buildUserCard(item) {
    var col = document.createElement('div');
    col.className = 'col-12 col-md-6 col-lg-4 col-xl-3';

    var card = document.createElement('div');
    card.className = 'card card-body mb-4 px-2 py-3 card-widget widget-user position-relative';

    var row = document.createElement('div');
    row.className = 'd-flex align-items-center';

    var avatarWrap = document.createElement('div');
    avatarWrap.className = 'flex-shrink-0 ml-1 mr-3';

    var avatarUrl = item && item.avatar_url ? String(item.avatar_url) : '';
    if (avatarUrl) {
      var avatarImg = document.createElement('img');
      avatarImg.src = avatarUrl;
      avatarImg.width = 50;
      avatarImg.height = 50;
      avatarImg.className = 'img-circle elevation-2';
      avatarImg.style.objectFit = 'cover';
      avatarImg.alt = 'User Avatar';
      avatarWrap.appendChild(avatarImg);
    } else {
      var avatar = document.createElement('span');
      avatar.className = 'img-circle elevation-2 d-inline-flex align-items-center justify-content-center bg-secondary';
      avatar.style.width = '50px';
      avatar.style.height = '50px';

      var avatarIcon = document.createElement('i');
      avatarIcon.className = 'far fa-user';
      avatar.appendChild(avatarIcon);
      avatarWrap.appendChild(avatar);
    }

    var body = document.createElement('div');
    body.className = 'flex-grow-1 ms-2';
    body.style.minWidth = '0';

    var usernameWrap = document.createElement('div');
    usernameWrap.className = 'my-0 font-weight-bold';

    var usernameLink = document.createElement('a');
    var username = item && item.username ? String(item.username) : '';
    usernameLink.href = '/user/' + encodeURIComponent(username) + '/';
    usernameLink.textContent = username;
    usernameWrap.appendChild(usernameLink);

    body.appendChild(usernameWrap);

    var fullName = item && item.full_name ? String(item.full_name) : '';
    if (fullName) {
      var fullNameEl = document.createElement('div');
      fullNameEl.className = 'text-truncate w-100';
      fullNameEl.textContent = fullName;
      body.appendChild(fullNameEl);
    }

    row.appendChild(avatarWrap);
    row.appendChild(body);
    card.appendChild(row);
    col.appendChild(card);

    return col;
  }

  function createPageItem(opts) {
    var li = document.createElement('li');
    li.className = 'page-item';

    if (opts.isDisabled) {
      li.classList.add('disabled');
    }
    if (opts.isActive) {
      li.classList.add('active');
    }

    if (opts.isEllipsis) {
      var span = document.createElement('span');
      span.className = 'page-link';
      span.textContent = '...';
      li.appendChild(span);
      return li;
    }

    var link = document.createElement('a');
    link.className = 'page-link';
    link.href = opts.href || '#';
    if (opts.ariaLabel) {
      link.setAttribute('aria-label', opts.ariaLabel);
    }
    link.textContent = opts.label || '';
    li.appendChild(link);
    return li;
  }

  function renderPagination(pagination) {
    var wrapper = document.createElement('div');
    wrapper.className = 'mt-2 clearfix';

    var summary = document.createElement('div');
    summary.className = 'float-left text-muted small';

    if (pagination && pagination.count) {
      summary.textContent =
        'Showing ' +
        String(pagination.start_index || 0) +
        '-' +
        String(pagination.end_index || 0) +
        ' of ' +
        String(pagination.count || 0);
    }
    wrapper.appendChild(summary);

    if (!pagination || !pagination.num_pages || pagination.num_pages <= 1) {
      return wrapper;
    }

    var ul = document.createElement('ul');
    ul.className = 'pagination pagination-sm m-0 float-right';

    var pageUrlPrefix = String(pagination.page_url_prefix || '');

    ul.appendChild(
      createPageItem({
        isDisabled: !pagination.has_previous,
        href: pagination.has_previous ? pageUrlPrefix + String(pagination.previous_page_number) : '#',
        label: '\u00ab',
        ariaLabel: 'Previous',
      })
    );

    if (pagination.show_first) {
      ul.appendChild(
        createPageItem({
          href: pageUrlPrefix + '1',
          label: '1',
        })
      );
      ul.appendChild(createPageItem({ isEllipsis: true, isDisabled: true }));
    }

    var pageNumbers = Array.isArray(pagination.page_numbers) ? pagination.page_numbers : [];
    for (var i = 0; i < pageNumbers.length; i += 1) {
      var pageNumber = pageNumbers[i];
      ul.appendChild(
        createPageItem({
          href: pageUrlPrefix + String(pageNumber),
          label: String(pageNumber),
          isActive: Number(pageNumber) === Number(pagination.page),
        })
      );
    }

    if (pagination.show_last) {
      ul.appendChild(createPageItem({ isEllipsis: true, isDisabled: true }));
      ul.appendChild(
        createPageItem({
          href: pageUrlPrefix + String(pagination.num_pages),
          label: String(pagination.num_pages),
        })
      );
    }

    ul.appendChild(
      createPageItem({
        isDisabled: !pagination.has_next,
        href: pagination.has_next ? pageUrlPrefix + String(pagination.next_page_number) : '#',
        label: '\u00bb',
        ariaLabel: 'Next',
      })
    );

    wrapper.appendChild(ul);
    return wrapper;
  }

  function renderUsers(root, payload) {
    var users = payload && Array.isArray(payload.users) ? payload.users : [];
    var pagination = payload && payload.pagination ? payload.pagination : null;
    var emptyLabel = payload && payload.empty_label ? String(payload.empty_label) : 'No users found.';

    root.innerHTML = '';

    if (!users.length) {
      var empty = document.createElement('div');
      empty.className = 'text-muted';
      empty.textContent = emptyLabel;
      root.appendChild(empty);
      root.appendChild(renderPagination(pagination));
      return;
    }

    var row = document.createElement('div');
    row.className = 'row';

    for (var i = 0; i < users.length; i += 1) {
      row.appendChild(buildUserCard(users[i]));
    }

    root.appendChild(row);
    root.appendChild(renderPagination(pagination));
  }

  function renderError(root) {
    root.innerHTML = '';
    var msg = document.createElement('div');
    msg.className = 'text-muted';
    msg.textContent = 'Unable to load users right now.';
    root.appendChild(msg);
  }

  function buildFetchUrlWithQuery(baseUrl, query) {
    var queryValue = query || '';
    if (!queryValue) return baseUrl;
    if (baseUrl.indexOf('?') === -1) return baseUrl + queryValue;
    return baseUrl + '&' + queryValue.replace('?', '');
  }

  function renderLoading(root) {
    root.innerHTML = '';
    var wrap = document.createElement('div');
    wrap.className = 'd-flex align-items-center text-muted';
    var spinner = document.createElement('span');
    spinner.className = 'spinner-border spinner-border-sm mr-2';
    spinner.setAttribute('role', 'status');
    spinner.setAttribute('aria-hidden', 'true');
    var label = document.createElement('span');
    label.textContent = 'Loading users...';
    wrap.appendChild(spinner);
    wrap.appendChild(label);
    root.appendChild(wrap);
  }

  function loadGrid(root, baseUrl, query, shouldPushState) {
    renderLoading(root);
    var url = buildFetchUrlWithQuery(baseUrl, query);
    fetch(url, { headers: { 'Accept': 'application/json' } })
      .then(function (resp) {
        if (!resp.ok) throw new Error('Failed to load users: ' + resp.status);
        return resp.json();
      })
      .then(function (payload) {
        renderUsers(root, payload);
        if (shouldPushState) {
          var targetQuery = query || '';
          var nextUrl = window.location.pathname + targetQuery;
          window.history.pushState({ usersGrid: true }, '', nextUrl);
        }
      })
      .catch(function () {
        renderError(root);
      });
  }

  function init() {
    var root = document.getElementById('users-grid-root');
    if (!root) return;

    var baseUrl = String(root.getAttribute('data-users-grid-url') || '').trim();
    if (!baseUrl) return;

    function findLinkTarget(target) {
      var node = target;
      while (node && node !== root) {
        if (node.tagName && node.tagName.toLowerCase() === 'a' && node.classList.contains('page-link')) {
          return node;
        }
        node = node.parentNode;
      }
      return null;
    }

    root.addEventListener('click', function (event) {
      if (event.defaultPrevented) return;
      if (event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

      var link = findLinkTarget(event.target);
      if (!link) return;

      var href = link.getAttribute('href');
      if (!href || href === '#') {
        event.preventDefault();
        return;
      }

      var li = link.parentNode;
      if (li && li.classList && li.classList.contains('disabled')) {
        event.preventDefault();
        return;
      }

      event.preventDefault();
      var dest = new URL(link.href, window.location.origin);
      if (dest.pathname !== window.location.pathname) return;
      loadGrid(root, baseUrl, dest.search, true);
    });

    window.addEventListener('popstate', function () {
      loadGrid(root, baseUrl, window.location.search, false);
    });

    loadGrid(root, baseUrl, window.location.search, false);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
