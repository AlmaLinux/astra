(function (window, document) {
  'use strict';

  function getJquery() {
    if (window.django && window.django.jQuery) {
      return window.django.jQuery;
    }

    if (window.jQuery) {
      return window.jQuery;
    }

    return null;
  }

  function initAjaxSelect2(jq) {
    if (!jq || !jq.fn || !jq.fn.select2) {
      return;
    }

    jq('select.alx-select2[data-ajax-url]').each(function (_index, element) {
      var wrapped = jq(element);
      if (wrapped.hasClass('select2-hidden-accessible')) {
        return;
      }

      var ajaxUrl = String(wrapped.attr('data-ajax-url') || '').trim();
      if (!ajaxUrl) {
        return;
      }

      try {
        wrapped.select2({
          width: '100%',
          allowClear: true,
          placeholder: String(wrapped.attr('data-placeholder') || '').trim(),
          ajax: {
            url: ajaxUrl,
            dataType: 'json',
            delay: 250,
            data: function (params) {
              return { q: String((params && params.term) || '').trim() };
            },
            processResults: function (data) {
              var results = data && Array.isArray(data.results) ? data.results : [];
              return { results: results };
            }
          },
          minimumInputLength: 1
        });
      } catch (_error) {
        // Leave the underlying select usable if Select2 initialization fails.
      }
    });
  }

  var jq = getJquery();
  if (jq) {
    jq(function () {
      initAjaxSelect2(jq);
    });
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      initAjaxSelect2(getJquery());
    });
  } else {
    initAjaxSelect2(getJquery());
  }
})(window, document);