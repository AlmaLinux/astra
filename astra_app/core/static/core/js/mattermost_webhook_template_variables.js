(function () {
  "use strict";

  // Show the template variables of the events ticked in the Routing tab, so they can be
  // consulted before saving. The server renders every event's section; this only toggles them.
  function syncTemplateVariables() {
    const selected = new Set(
      Array.from(document.querySelectorAll('input[name="events"]:checked'), (input) => input.value),
    );

    for (const section of document.querySelectorAll("[data-template-variables-event]")) {
      section.hidden = !selected.has(section.dataset.templateVariablesEvent);
    }

    const emptyHint = document.querySelector("[data-template-variables-empty]");
    if (emptyHint) {
      emptyHint.hidden = selected.size > 0;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.addEventListener("change", (event) => {
      if (event.target.matches('input[name="events"]')) {
        syncTemplateVariables();
      }
    });
    // Browsers may restore ticked boxes on back/forward navigation, unlike the server render.
    syncTemplateVariables();
  });
})();
