(function (window, document) {
  'use strict';

  if (window.AstraMembershipRequestSharedModalsInitialized) {
    return;
  }
  window.AstraMembershipRequestSharedModalsInitialized = true;

  function setText(modal, selector, value) {
    var el = modal.querySelector(selector);
    if (el) {
      el.textContent = value || '';
    }
  }

  function resetFormFields(modal) {
    var textarea = modal.querySelector('textarea');
    if (textarea) {
      textarea.value = '';
    }

    var select = modal.querySelector('select[data-preset-textarea-target]');
    if (select) {
      select.selectedIndex = 0;
    }
  }

  function onModalShow(event) {
    var trigger = event.relatedTarget;
    if (!trigger) return;

    var modal = event.target;

    var title = trigger.getAttribute('data-modal-title');
    if (title) {
      setText(modal, '.modal-title', title);
    }

    var actionUrl = trigger.getAttribute('data-action-url');
    if (actionUrl) {
      var form = modal.querySelector('form');
      if (form) {
        form.setAttribute('action', actionUrl);
      }
    }

    setText(modal, '.js-body-prefix', trigger.getAttribute('data-body-prefix'));
    setText(modal, '.js-body-emphasis', trigger.getAttribute('data-body-emphasis'));
    setText(modal, '.js-body-suffix', trigger.getAttribute('data-body-suffix'));

    resetFormFields(modal);
  }

  function bindModal(modalId) {
    var modal = document.getElementById(modalId);
    if (!modal || !window.jQuery) return;
    window.jQuery(modal).on('show.bs.modal', onModalShow);
  }

  function init() {
    ['shared-approve-modal', 'shared-reject-modal', 'shared-rfi-modal', 'shared-ignore-modal'].forEach(bindModal);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})(window, document);
