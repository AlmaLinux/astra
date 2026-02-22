(function (window, document) {
  'use strict';

  function $(id) {
    return document.getElementById(id);
  }

  function getCsrfToken() {
    var el = document.querySelector('input[name=csrfmiddlewaretoken]');
    return el ? String(el.value || '') : '';
  }

  function parseCandidateId(el) {
    if (!el) return null;
    var raw = el.getAttribute('data-candidate-id');
    if (!raw) return null;
    var num = Number(raw);
    return Number.isFinite(num) ? num : null;
  }

  function readRankingFromDom() {
    var items = document.querySelectorAll('#election-ranking-list [data-candidate-id]');
    var ranking = [];
    for (var i = 0; i < items.length; i++) {
      var cid = parseCandidateId(items[i]);
      if (cid == null) continue;
      ranking.push(cid);
    }
    return ranking;
  }

  function syncRankingField() {
    var hidden = $('election-ranking-input');
    if (!hidden) return;
    hidden.value = readRankingFromDom().join(',');
  }

  function updateRankingNumbers() {
    var list = $('election-ranking-list');
    if (!list) return;
    var items = list.querySelectorAll('[data-candidate-id]');
    for (var i = 0; i < items.length; i++) {
      var badge = items[i].querySelector('[data-rank-number]');
      if (!badge) continue;
      badge.textContent = String(i + 1);
    }
  }

  function updateRankingGuidance(form) {
    var hint = $('election-ranking-hint');
    var order = $('election-ranking-order');
    var rankingField = $('election-ranking-input');
    var fallbackField = form ? form.querySelector('input[name="ranking_usernames"]') : null;

    var rankingEmpty = !rankingField || !String(rankingField.value || '').trim();
    var fallbackEmpty = !fallbackField || !String(fallbackField.value || '').trim();

    if (!hint) return;

    if (rankingEmpty && fallbackEmpty) {
      hint.classList.remove('d-none');
      order.classList.add('d-none');
    } else {
      hint.classList.add('d-none');
      order.classList.remove('d-none');
    }
  }

  function setRankingError(text) {
    var box = $('election-ranking-error');
    if (!box) return;
    box.textContent = text;
    box.classList.remove('d-none');

    var hint = $('election-ranking-hint');
    if (hint) hint.classList.add('d-none');
  }

  function clearRankingError() {
    var box = $('election-ranking-error');
    if (!box) return;
    box.textContent = '';
    box.classList.add('d-none');
  }

  function getSubmitButton() {
    var btn = $('election-submit-button');
    if (btn) return btn;
    var form = $('election-vote-form');
    return form ? form.querySelector('button[type="submit"], input[type="submit"]') : null;
  }

  function setSubmitLabel(text) {
    var btn = getSubmitButton();
    if (!btn) return;
    btn.textContent = String(text || 'Submit vote');
  }

  function setSubmitVisible(visible) {
    var btn = getSubmitButton();
    if (!btn) return;
    if (visible) {
      btn.classList.remove('d-none');
    } else {
      btn.classList.add('d-none');
    }
  }

  function setResult(text, isError) {
    var box = $('election-vote-result');
    if (!box) return;
    box.classList.remove('alert-success', 'alert-danger');
    box.classList.add(isError ? 'alert-danger' : 'alert-success');
    box.textContent = text;
    box.classList.remove('d-none');

    var receiptBox = $('election-receipt-box');
    if (receiptBox) receiptBox.classList.add('d-none');

    if (isError) {
      setSubmitVisible(true);
      setSubmitLabel('Submit vote');
    }
  }

  function setReceipt(receipt) {
    var receiptBox = $('election-receipt-box');
    var receiptInput = $('election-receipt');
    if (!receiptBox || !receiptInput) return;
    receiptInput.value = receipt;
    receiptBox.classList.remove('d-none');
    setSubmitVisible(true);
    setSubmitLabel('Submit replacement ballot');
  }

  function setReceiptDetails(details) {
    if (!details) return;

    var receipt = String(details.ballot_hash || '');
    if (receipt) setReceipt(receipt);

    var verifyLink = $('election-receipt-verify');
    var form = $('election-vote-form');
    var verifyBase = form ? String(form.getAttribute('data-verify-url') || '') : '';
    if (verifyLink && verifyBase && receipt) {
      verifyLink.href = verifyBase + '?receipt=' + encodeURIComponent(receipt);
    }

    var nonceInput = $('election-nonce');
    if (nonceInput) nonceInput.value = String(details.nonce || '');

    var prevInput = $('election-previous-chain-hash');
    if (prevInput) prevInput.value = String(details.previous_chain_hash || '');

    var chainInput = $('election-chain-hash');
    if (chainInput) chainInput.value = String(details.chain_hash || '');
  }

  function attachReceiptCopy() {
    var btn = $('election-receipt-copy');
    var receiptInput = $('election-receipt');
    if (!btn || !receiptInput) return;

    btn.addEventListener('click', async function () {
      var text = String(receiptInput.value || '');
      if (!text) return;
      try {
        if (navigator && navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(text);
          setResult('Receipt copied to clipboard.', false);
          setReceipt(text);
          return;
        }
      } catch (_e) {
        // Fall back to selection-based copy.
      }

      receiptInput.focus();
      receiptInput.select();
      try {
        document.execCommand('copy');
        setResult('Receipt copied to clipboard.', false);
        setReceipt(text);
      } catch (_e2) {
        setResult('Copy failed. Please copy the receipt manually.', true);
        setReceipt(text);
      }
    });
  }

  function prefillCredentialFromUrlFragment() {
    var input = $('election-credential');
    if (!input) return;
    var hash = String(window.location.hash || '');
    if (!hash || hash.length < 2) return;

    var params = new window.URLSearchParams(hash.slice(1));
    var cred = String(params.get('credential') || '').trim();
    if (!cred) return;

    input.value = cred;

    // Remove the secret from the address bar (history) after we captured it.
    try {
      window.history.replaceState(null, document.title, window.location.pathname + window.location.search);
    } catch (_e) {
      // If replaceState is not available, leave the URL as-is.
    }
  }

  function attachRankingButtons() {
    var addButtons = document.querySelectorAll('[data-action="election-add"]');
    var list = $('election-ranking-list');
    if (!list) return;

    var form = $('election-vote-form');

    function addCandidate(cid, label) {
      var existing = list.querySelector('[data-candidate-id="' + String(cid) + '"]');
      if (existing) return;

      var item = document.createElement('div');
      item.className = 'd-flex align-items-center justify-content-between border rounded px-2 py-1 mb-2';
      item.setAttribute('data-candidate-id', String(cid));

      var left = document.createElement('div');
      left.className = 'd-flex align-items-center text-truncate pr-2';

      var number = document.createElement('span');
      number.className = 'badge badge-secondary mr-2';
      number.setAttribute('data-rank-number', '1');
      number.textContent = '1';

      var labelSpan = document.createElement('span');
      labelSpan.className = 'text-truncate';
      labelSpan.textContent = label;

      left.appendChild(number);
      left.appendChild(labelSpan);

      var right = document.createElement('div');

      var up = document.createElement('button');
      up.type = 'button';
      up.className = 'btn btn-sm btn-outline-secondary mr-1';
      up.textContent = '↑';
      up.addEventListener('click', function () {
        var prev = item.previousElementSibling;
        if (prev) list.insertBefore(item, prev);
        syncRankingField();
        updateRankingNumbers();
        clearRankingError();
        updateRankingGuidance(form);
      });

      var down = document.createElement('button');
      down.type = 'button';
      down.className = 'btn btn-sm btn-outline-secondary mr-1';
      down.textContent = '↓';
      down.addEventListener('click', function () {
        var next = item.nextElementSibling;
        if (next) list.insertBefore(next, item);
        syncRankingField();
        updateRankingNumbers();
        clearRankingError();
        updateRankingGuidance(form);
      });

      var remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'btn btn-sm btn-outline-danger';
      remove.textContent = 'Remove';
      remove.addEventListener('click', function () {
        item.remove();
        syncRankingField();
        updateRankingNumbers();
        clearRankingError();
        updateRankingGuidance(form);
      });

      right.appendChild(up);
      right.appendChild(down);
      right.appendChild(remove);

      item.appendChild(left);
      item.appendChild(right);
      list.appendChild(item);
      syncRankingField();
      updateRankingNumbers();
      clearRankingError();
      updateRankingGuidance(form);
    }

    for (var i = 0; i < addButtons.length; i++) {
      (function (btn) {
        btn.addEventListener('click', function () {
          var cid = parseCandidateId(btn);
          var label = btn.getAttribute('data-candidate-label') || 'Candidate ' + String(cid);
          if (cid == null) return;
          addCandidate(cid, label);
        });
      })(addButtons[i]);
    }
  }

  async function attachSubmitHandler() {
    var form = $('election-vote-form');
    if (!form) return;

    function validateRankingOrShowError() {
      syncRankingField();

      var rankingField = $('election-ranking-input');
      var fallbackField = form.querySelector('input[name="ranking_usernames"]');
      var rankingEmpty = rankingField && !String(rankingField.value || '').trim();
      var fallbackEmpty = !fallbackField || !String(fallbackField.value || '').trim();

      if (rankingEmpty && fallbackEmpty) {
        setRankingError('Add a candidate to your ranking from the "Candidates" box.');
        return false;
      }

      clearRankingError();
      updateRankingGuidance(form);
      return true;
    }

    // If the credential field is empty, the browser will prevent form submission and
    // our submit handler will never run. Validate ranking on user intent instead.
    var submitButton = form.querySelector('button[type="submit"], input[type="submit"]');
    if (submitButton) {
      submitButton.addEventListener('click', function () {
        validateRankingOrShowError();
      });
    }

    form.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') {
        validateRankingOrShowError();
      }
    });

    var fallbackField = form.querySelector('input[name="ranking_usernames"]');
    if (fallbackField) {
      fallbackField.addEventListener('input', function () {
        clearRankingError();
        updateRankingGuidance(form);
      });
    }

    form.addEventListener('submit', async function (ev) {
      ev.preventDefault();
      if (!validateRankingOrShowError()) return;

      var data = new window.FormData(form);

      try {
        var resp = await window.fetch(form.action, {
          method: 'POST',
          headers: { 'X-CSRFToken': getCsrfToken(), 'Accept': 'application/json' },
          body: data
        });

        var payload = await resp.json();
        if (!resp.ok) {
          setResult(payload && payload.error ? payload.error : 'Vote submission failed.', true);
          return;
        }

        setResult('Vote recorded.', false);
        setReceiptDetails(payload);
      } catch (e) {
        setResult('Vote submission failed.', true);
      }
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    attachRankingButtons();
    attachSubmitHandler();
    attachReceiptCopy();
    prefillCredentialFromUrlFragment();
    syncRankingField();
    updateRankingNumbers();

    var form = $('election-vote-form');
    if (form) updateRankingGuidance(form);

    setSubmitLabel('Submit vote');
  });

  // Initialize vote breakdown tooltip with html:true.
  // We don't use data-toggle="tooltip" on this element to prevent AdminLTE's
  // global init (which uses html:false) from capturing it first.
  var breakdownBtn = document.getElementById('vote-breakdown-tooltip');
  if (breakdownBtn) {
    var breakdownContent = document.getElementById('vote-breakdown-tooltip-content');
    window.jQuery(breakdownBtn).tooltip({
      html: true,
      placement: breakdownBtn.getAttribute('data-placement') || 'right',
      title: function () {
        return breakdownContent ? breakdownContent.innerHTML : '';
      },
      template: '<div class="tooltip vote-breakdown-tooltip" role="tooltip"><div class="arrow"></div><div class="tooltip-inner text-left"></div></div>'
    });
  }
})(window, document);
