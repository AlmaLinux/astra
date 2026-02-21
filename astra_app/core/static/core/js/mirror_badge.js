(function () {
  function showLoading(container) {
    var loading = container.querySelector('[data-mirror-badge-loading]');
    if (!loading) return;
    loading.classList.remove('d-none');
  }

  function extractMirrorUrlFromBadgeImgSrc(imgSrc) {
    try {
      const src = new URL(imgSrc, window.location.origin);
      return src.searchParams.get("url") || "";
    } catch {
      return "";
    }
  }

  async function _fetchMirrorBadgeTooltip(containerEl, mirrorUrl) {
    const endpointUrl = containerEl.dataset.mirrorBadgeStatusEndpointUrl;
    if (!endpointUrl) {
      return;
    }

    const url = new URL(endpointUrl, window.location.origin);
    url.searchParams.set("url", mirrorUrl);

    let response;
    try {
      response = await fetch(url.toString(), {
        method: "GET",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
        },
      });
    } catch {
      return;
    }

    if (!response.ok) {
      return;
    }

    let payload;
    try {
      payload = await response.json();
    } catch {
      return;
    }

    if (!payload || typeof payload.tooltip !== "string") {
      return;
    }

    const img = containerEl.querySelector("[data-mirror-badge-img]");
    if (!img) {
      return;
    }

    // Keep title as the single source of truth for the tooltip.
    img.title = payload.tooltip;
    img.setAttribute("aria-label", payload.tooltip);
  }

  function hideLoading(container) {
    var loading = container.querySelector('[data-mirror-badge-loading]');
    if (!loading) return;
    loading.classList.add('d-none');
  }

  function hideBadge(container) {
    var img = container.querySelector('[data-mirror-badge-img]');
    if (!img) return;
    img.classList.add('d-none');
  }

  function showBadge(container) {
    var img = container.querySelector('[data-mirror-badge-img]');
    if (!img) return;
    img.classList.remove('d-none');
  }

  function initContainer(container) {
    var img = container.querySelector('[data-mirror-badge-img]');
    if (!img) {
      hideLoading(container);
      return;
    }

    img.addEventListener(
      'load',
      function () {
        showBadge(container);
        hideLoading(container);
      },
      { once: true }
    );

    // Fetch tooltip in parallel; don't block rendering.
    // This also works even if the SVG was cached.
    var mirrorUrl = extractMirrorUrlFromBadgeImgSrc(img.src);
    if (mirrorUrl) {
      void _fetchMirrorBadgeTooltip(container, mirrorUrl);
    }

    img.addEventListener(
      'error',
      function () {
        hideLoading(container);
      },
      { once: true }
    );

    if (img.complete) {
      if (img.naturalWidth > 0) {
        showBadge(container);
      }
      hideLoading(container);
    }
  }

  function normalizeMirrorUrl(value) {
    var raw = (value || '').trim();
    if (!raw) return '';
    if (raw.indexOf('://') >= 0) return raw;
    return 'https://' + raw;
  }

  function buildBadgeUrl(endpointUrl, mirrorUrl) {
    if (!endpointUrl || !mirrorUrl) return '';
    var separator = endpointUrl.indexOf('?') === -1 ? '?' : '&';
    return endpointUrl + separator + 'url=' + encodeURIComponent(mirrorUrl);
  }

  function initFormPreview(container) {
    var endpointUrl = container.getAttribute('data-mirror-badge-endpoint-url') || '';
    var img = container.querySelector('[data-mirror-badge-img]');
    var domainInput = document.getElementById('id_q_domain');
    if (!endpointUrl || !img || !domainInput) {
      hideLoading(container);
      return;
    }

    var debounceMs = 300;
    var timerId = null;
    var requestVersion = 0;

    function updatePreview() {
      var normalized = normalizeMirrorUrl(domainInput.value);
      if (!normalized) {
        requestVersion += 1;
        hideLoading(container);
        hideBadge(container);
        img.removeAttribute('src');
        return;
      }

      var nextSrc = buildBadgeUrl(endpointUrl, normalized);
      if (!nextSrc) {
        hideLoading(container);
        hideBadge(container);
        return;
      }

      requestVersion += 1;
      var currentVersion = requestVersion;
      showLoading(container);
      hideBadge(container);

      img.addEventListener(
        'load',
        function () {
          if (currentVersion !== requestVersion) return;
          showBadge(container);
          hideLoading(container);
        },
        { once: true }
      );

      void _fetchMirrorBadgeTooltip(container, normalized);

      img.addEventListener(
        'error',
        function () {
          if (currentVersion !== requestVersion) return;
          hideLoading(container);
          hideBadge(container);
        },
        { once: true }
      );

      img.src = nextSrc;
    }

    function scheduleUpdate() {
      if (timerId !== null) {
        clearTimeout(timerId);
      }
      timerId = setTimeout(updatePreview, debounceMs);
    }

    domainInput.addEventListener('input', scheduleUpdate);
    domainInput.addEventListener('change', scheduleUpdate);
    updatePreview();
  }

  function initMirrorBadges() {
    var containers = document.querySelectorAll('[data-mirror-badge-container]');
    for (var i = 0; i < containers.length; i += 1) {
      initContainer(containers[i]);
    }

    var formContainers = document.querySelectorAll('[data-mirror-badge-form-container]');
    for (var j = 0; j < formContainers.length; j += 1) {
      initFormPreview(formContainers[j]);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initMirrorBadges);
  } else {
    initMirrorBadges();
  }
})();
