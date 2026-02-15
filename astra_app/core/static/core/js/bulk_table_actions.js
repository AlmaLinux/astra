(function (window, document) {
  'use strict';

  function asArray(nodeList) {
    return Array.prototype.slice.call(nodeList || []);
  }

  function getCheckboxes(selector) {
    if (!selector) return [];
    return asArray(document.querySelectorAll(selector));
  }

  function setupForm(form) {
    if (!form) return;

    var selectAllId = String(form.getAttribute('data-bulk-select-all-id') || '').trim();
    var applyButtonId = String(form.getAttribute('data-bulk-apply-id') || '').trim();
    var checkboxSelector = String(form.getAttribute('data-bulk-checkbox-selector') || '').trim();

    if (!selectAllId || !applyButtonId || !checkboxSelector) return;

    var selectAll = document.getElementById(selectAllId);
    var applyButton = document.getElementById(applyButtonId);
    if (!selectAll || !applyButton) return;

    function updateApplyButtonState() {
      var checkboxes = getCheckboxes(checkboxSelector);
      var anyChecked = checkboxes.some(function (checkbox) { return checkbox.checked; });
      var actionSelect = form.querySelector('select[name="bulk_action"]');
      var selectedAction = actionSelect ? String(actionSelect.value || '').trim() : '';
      applyButton.disabled = !(anyChecked && selectedAction);
    }

    selectAll.addEventListener('change', function () {
      var checked = !!selectAll.checked;
      getCheckboxes(checkboxSelector).forEach(function (checkbox) {
        checkbox.checked = checked;
      });
      updateApplyButtonState();
    });

    document.addEventListener('change', function (event) {
      var target = event.target;
      if (!target) return;

      if (target.matches && target.matches(checkboxSelector)) {
        var checkboxes = getCheckboxes(checkboxSelector);
        selectAll.checked = checkboxes.length > 0 && checkboxes.every(function (checkbox) { return checkbox.checked; });
        updateApplyButtonState();
        return;
      }

      if (target.name === 'bulk_action' && form.contains(target)) {
        updateApplyButtonState();
      }
    });

    updateApplyButtonState();
  }

  function initBulkTableActions(root) {
    var container = root || document;
    var forms = asArray(container.querySelectorAll('[data-bulk-table-form]'));
    forms.forEach(setupForm);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      initBulkTableActions(document);
    });
  } else {
    initBulkTableActions(document);
  }

  window.AstraBulkTableActions = {
    init: initBulkTableActions,
  };
})(window, document);
