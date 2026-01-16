/* global jQuery */

(function ($) {
  'use strict';

  function parseMaxlength(el) {
    const raw = el.getAttribute('maxlength');
    if (!raw) return null;
    const n = Number.parseInt(raw, 10);
    if (!Number.isFinite(n) || n <= 0) return null;
    return n;
  }

  function formatCount(valueLen, maxLen) {
    return valueLen + '/' + maxLen;
  }

  function attachCharacterCount(el) {
    if (el.dataset.characterCountAttached === '1') return;
    if (el.dataset.characterCount === 'false') return;

    const maxLen = parseMaxlength(el);
    if (maxLen === null) return;

    const $el = $(el);

    const $counter = $('<div />', {
      class: 'text-muted text-sm mt-1 js-character-count',
      'aria-live': 'polite',
    });

    function update() {
      const valueLen = String($el.val() || '').length;
      $counter.text(formatCount(valueLen, maxLen));

      // Subtle visual hint when exceeding max (some browsers allow paste beyond maxlength).
      if (valueLen > maxLen) {
        $counter.addClass('text-danger');
      } else {
        $counter.removeClass('text-danger');
      }
    }

    $counter.insertAfter($el);

    $el.on('input change', update);
    update();

    el.dataset.characterCountAttached = '1';
  }

  $(function () {
    $('textarea[maxlength], input[type="text"][maxlength]').each(function () {
      attachCharacterCount(this);
    });
  });
})(jQuery);
