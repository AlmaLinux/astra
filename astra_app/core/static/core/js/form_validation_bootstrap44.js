(function () {
  "use strict";

  const fieldSelector = "input, select, textarea";

  function isValidField(target) {
    return target instanceof HTMLElement && target.matches(fieldSelector);
  }

  function syncRequiredIndicatorsForField(form, field) {
    if (!field.id) {
      return;
    }

    const isRequired = Boolean(field.required);
    for (const indicator of form.querySelectorAll(
      `[data-required-indicator-for="${field.id}"]`
    )) {
      indicator.classList.toggle("d-none", !isRequired);
    }
    for (const indicatorText of form.querySelectorAll(
      `[data-required-indicator-text-for="${field.id}"]`
    )) {
      indicatorText.classList.toggle("d-none", !isRequired);
    }
  }

  function syncRequiredIndicators(form) {
    for (const field of form.querySelectorAll(fieldSelector)) {
      if (isValidField(field)) {
        syncRequiredIndicatorsForField(form, field);
      }
    }
  }

  function applyInvalidState(field) {
    if (field.willValidate) {
      field.classList.toggle("is-invalid", !field.checkValidity());
    } else {
      field.classList.remove("is-invalid");
    }

    const inputGroup = field.closest(".input-group");
    if (inputGroup) {
      inputGroup.classList.toggle("is-invalid", field.classList.contains("is-invalid"));
    }
  }

  function validateField(form, trigger, field) {
    dispatchValidationHook(form, trigger, field);
    syncRequiredIndicatorsForField(form, field);
    applyInvalidState(field);
  }

  function dispatchValidationHook(form, trigger, target) {
    form.dispatchEvent(
      new CustomEvent("astra:validate-form", {
        bubbles: false,
        detail: {
          trigger,
          target,
        },
      })
    );
  }

  function validateForm(form, trigger, target) {
    dispatchValidationHook(form, trigger, target);
    syncRequiredIndicators(form);
    form.classList.add("was-validated");
    return form.checkValidity();
  }

  const forms = document.querySelectorAll("form.needs-validation[novalidate]");
  for (const form of forms) {
    form.addEventListener(
      "submit",
      function (event) {
        const allowInvalidSubmit =
          event.submitter && event.submitter.dataset.allowInvalidSubmit === "true";
        const isValid = validateForm(form, "submit", event.target);
        if (!isValid && !allowInvalidSubmit) {
          event.preventDefault();
          event.stopPropagation();
        }
      },
      false
    );

    syncRequiredIndicators(form);

    for (const eventName of ["input", "change", "blur"]) {
      form.addEventListener(
        eventName,
        function (event) {
          const { target } = event;
          if (!isValidField(target)) {
            dispatchValidationHook(form, eventName, target);
            return;
          }

          const field = target;
          if (eventName === "blur") {
            field.dataset.astraTouched = "1";
            validateField(form, eventName, field);
            return;
          }

          if (
            field.dataset.astraTouched === "1" || field.classList.contains("is-invalid")
          ) {
            validateField(form, eventName, field);
          } else {
            dispatchValidationHook(form, eventName, field);
            syncRequiredIndicatorsForField(form, field);
          }
        },
        true
      );
    }
  }
})();
