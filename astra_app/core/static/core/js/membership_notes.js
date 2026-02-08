/**
 * Membership Committee Notes — AJAX posting, collapse state via localStorage,
 * scroll-to-bottom, vote actions.
 *
 * Each note widget calls AstraMembershipNotes.init(pk) after this file loads.
 * The PK is read from the DOM via data-membership-notes-container attributes.
 */
(function () {
  'use strict';
  if (window.AstraMembershipNotes && window.AstraMembershipNotes._initialized) return;

  var ns = window.AstraMembershipNotes = window.AstraMembershipNotes || {};
  ns._initialized = true;

  ns._storageKey = function (requestPk) {
    return 'membership_notes_open_' + requestPk;
  };

  ns.init = function (requestPk) {
    var container = document.getElementById('membership-notes-container-' + requestPk);
    if (!container || container.dataset.membershipNotesInitialized) return;
    container.dataset.membershipNotesInitialized = '1';

    var card = document.getElementById('membership-notes-card-' + requestPk);
    var form = document.getElementById('membership-notes-form-' + requestPk);
    var message = document.getElementById('membership-notes-message-' + requestPk);
    var collapseBtn = container.querySelector('[data-membership-notes-collapse="' + requestPk + '"]');
    var header = container.querySelector('[data-membership-notes-header="' + requestPk + '"]');

    function getMessagesEl() {
      return container.querySelector('[data-membership-notes-messages="' + requestPk + '"]');
    }

    function setOpenState(isOpen) {
      try {
        window.localStorage.setItem(ns._storageKey(requestPk), isOpen ? '1' : '0');
      } catch (e) {
        // Ignore storage errors.
      }
    }

    function readStoredOpenState() {
      try {
        return window.localStorage.getItem(ns._storageKey(requestPk));
      } catch (e) {
        return null;
      }
    }

    function getDesiredOpenState() {
      var stored = readStoredOpenState();
      if (stored === '1') return true;
      if (stored === '0') return false;
      return (container && container.dataset && container.dataset.membershipNotesDefaultOpen === '1');
    }

    function ensureDesiredOpenState() {
      if (!card || !collapseBtn) return;
      var desiredOpen = getDesiredOpenState();
      var isCollapsed = card.classList.contains('collapsed-card');

      if (desiredOpen && isCollapsed) {
        collapseBtn.click();
        scrollToBottomSoon();
      } else if (!desiredOpen && !isCollapsed) {
        collapseBtn.click();
      }
    }

    function scrollToBottom() {
      var msgs = getMessagesEl();
      if (!msgs) return;
      msgs.scrollTop = msgs.scrollHeight;
    }

    function scrollToBottomWhenReady(maxWaitMs) {
      if (!card) {
        scrollToBottom();
        return;
      }

      var start = Date.now();

      function tick() {
        var msgs = getMessagesEl();
        if (!msgs) return;

        var isOpen = !card.classList.contains('collapsed-card');
        var isVisible = msgs.clientHeight > 0;

        if (isOpen && isVisible) {
          msgs.scrollTop = msgs.scrollHeight;
          setTimeout(function () {
            var msgs2 = getMessagesEl();
            if (msgs2) msgs2.scrollTop = msgs2.scrollHeight;
          }, 0);
          return;
        }

        if (Date.now() - start > maxWaitMs) {
          msgs.scrollTop = msgs.scrollHeight;
          return;
        }

        setTimeout(tick, 50);
      }

      tick();
    }

    function scrollToBottomSoon() {
      scrollToBottom();
      // Expand uses an animation (~1s); wait until the element is measurable.
      scrollToBottomWhenReady(2000);
    }

    function ajaxSubmit(action) {
      if (!form) return;
      setOpenState(true);
      ensureDesiredOpenState();

      var errorBox = document.getElementById('membership-notes-error-' + requestPk);
      var errorText = document.getElementById('membership-notes-error-text-' + requestPk);
      if (errorBox) {
        errorBox.classList.add('d-none');
      }
      if (errorText) {
        errorText.textContent = '';
      }

      var actionInput = form.querySelector('input[name="note_action"]');
      if (actionInput) actionInput.value = action;

      var formData = new FormData(form);
      formData.set('note_action', action);

      var buttons = form.querySelectorAll('button');
      for (var i = 0; i < buttons.length; i++) buttons[i].disabled = true;

      fetch(form.action, {
        method: 'POST',
        body: formData,
        headers: {
          'X-Requested-With': 'XMLHttpRequest'
        },
        credentials: 'same-origin'
      })
        .then(function (resp) {
          return resp.json().then(function (data) {
            return { status: resp.status, data: data };
          });
        })
        .then(function (result) {
          if (!result || !result.data || !result.data.ok || !result.data.html) {
            throw new Error((result && result.data && result.data.error) || 'Failed');
          }

          var currentContainer = document.getElementById('membership-notes-container-' + requestPk);
          if (!currentContainer) return;

          var tmp = document.createElement('div');
          tmp.innerHTML = result.data.html;
          var newContainer = tmp.querySelector('#membership-notes-container-' + requestPk);
          if (!newContainer) return;

          var newMessages = newContainer.querySelector('[data-membership-notes-messages="' + requestPk + '"]');
          var currentMessages = getMessagesEl();
          if (currentMessages && newMessages) {
            currentMessages.innerHTML = newMessages.innerHTML;
          }

          var newCount = newContainer.querySelector('[data-membership-notes-count="' + requestPk + '"]');
          var curCounts = currentContainer.querySelectorAll('[data-membership-notes-count="' + requestPk + '"]');
          if (newCount && curCounts && curCounts.length) {
            for (var cIdx = 0; cIdx < curCounts.length; cIdx++) {
              curCounts[cIdx].textContent = newCount.textContent;
              curCounts[cIdx].setAttribute('title', newCount.getAttribute('title') || '');
            }
          }

          var newApprovals = newContainer.querySelector('[data-membership-notes-approvals="' + requestPk + '"]');
          var curApprovals = currentContainer.querySelectorAll('[data-membership-notes-approvals="' + requestPk + '"]');
          if (newApprovals && curApprovals && curApprovals.length) {
            for (var aIdx = 0; aIdx < curApprovals.length; aIdx++) {
              curApprovals[aIdx].innerHTML = newApprovals.innerHTML;
            }
          }

          var newDisapprovals = newContainer.querySelector('[data-membership-notes-disapprovals="' + requestPk + '"]');
          var curDisapprovals = currentContainer.querySelectorAll('[data-membership-notes-disapprovals="' + requestPk + '"]');
          if (newDisapprovals && curDisapprovals && curDisapprovals.length) {
            for (var dIdx = 0; dIdx < curDisapprovals.length; dIdx++) {
              curDisapprovals[dIdx].innerHTML = newDisapprovals.innerHTML;
            }
          }

          if (message) {
            message.value = '';
          }
          scrollToBottomSoon();
        })
        .catch(function (err) {
          var box = document.getElementById('membership-notes-error-' + requestPk);
          var text = document.getElementById('membership-notes-error-text-' + requestPk);
          if (box) {
            var msg = (err && err.message) ? String(err.message) : '';
            if (!msg || msg === 'Failed') {
              msg = 'Could not post note right now. Please try again.';
            }
            if (text) {
              text.textContent = msg;
            }
            box.classList.remove('d-none');
          }
        })
        .finally(function () {
          var btns = form.querySelectorAll('button');
          for (var j = 0; j < btns.length; j++) btns[j].disabled = false;
        });
    }

    if (collapseBtn && card) {
      // Prefer AdminLTE CardWidget events when available.
      card.addEventListener('expanded.lte.cardwidget', function () {
        setOpenState(true);
        scrollToBottomSoon();
      });
      card.addEventListener('collapsed.lte.cardwidget', function () {
        setOpenState(false);
      });

      collapseBtn.addEventListener('click', function () {
        var wasCollapsed = card.classList.contains('collapsed-card');
        setTimeout(function () {
          var isOpen = !card.classList.contains('collapsed-card');
          setOpenState(isOpen);
          if (wasCollapsed && isOpen) {
            // Fallback when the custom events aren't fired.
            scrollToBottomSoon();
          }
        }, 0);
      });
    }

    if (header && collapseBtn) {
      header.addEventListener('click', function (e) {
        if (!e) return;
        // Don't double-toggle when clicking the card tools (plus button, badges).
        var t = e.target;
        if (t && t.closest && t.closest('.card-tools')) return;
        var wasCollapsed = card && card.classList.contains('collapsed-card');
        collapseBtn.click();
        if (wasCollapsed) scrollToBottomSoon();
      });

      header.addEventListener('keydown', function (e) {
        if (!e) return;
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          var wasCollapsed2 = card && card.classList.contains('collapsed-card');
          collapseBtn.click();
          if (wasCollapsed2) scrollToBottomSoon();
        }
      });
    }

    if (form) {
      form.addEventListener('submit', function (e) {
        if (!e) return;
        e.preventDefault();
        ajaxSubmit('message');
      });

      var voteButtons = form.querySelectorAll('[data-note-action]');
      for (var i2 = 0; i2 < voteButtons.length; i2++) {
        voteButtons[i2].addEventListener('click', function (e) {
          if (!e || !e.currentTarget) return;
          var action = e.currentTarget.getAttribute('data-note-action') || 'message';
          ajaxSubmit(action);
        });
      }
    }

    if (message) {
      message.addEventListener('keydown', function (e) {
        if (!e) return;
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
          e.preventDefault();
          ajaxSubmit('message');
        }
      });
    }

    var errorClose = container.querySelector('[data-membership-notes-error-close="' + requestPk + '"]');
    if (errorClose) {
      errorClose.addEventListener('click', function () {
        var box = document.getElementById('membership-notes-error-' + requestPk);
        var text = document.getElementById('membership-notes-error-text-' + requestPk);
        if (text) text.textContent = '';
        if (box) box.classList.add('d-none');
      });
    }

    ensureDesiredOpenState();
    scrollToBottomSoon();
  };
})();
