(function (window, document) {
  'use strict';

  function runInit() {
    if (!window.TemplatedEmailComposeRegistry || !window.TemplatedEmailComposeRegistry.initAll) {
      return;
    }
    window.TemplatedEmailComposeRegistry.initAll(document);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', runInit);
  } else {
    runInit();
  }
})(window, document);
