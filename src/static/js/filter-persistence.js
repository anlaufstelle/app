/**
 * Filter-State Persistence via sessionStorage
 *
 * Saves filter values (selects, text inputs) within [data-filter-persist]
 * containers to sessionStorage and restores them on page load when the URL
 * has no query parameters (direct navigation without explicit filters).
 *
 * Key format: filters:<pathname>
 * Value: JSON object mapping input/select names to their values.
 */
(function () {
  "use strict";

  var STORAGE_PREFIX = "filters:";

  function getStorageKey() {
    return STORAGE_PREFIX + window.location.pathname;
  }

  /**
   * Collect all named filter elements inside [data-filter-persist] containers.
   */
  function getFilterElements() {
    var containers = document.querySelectorAll("[data-filter-persist]");
    var elements = [];
    containers.forEach(function (container) {
      var inputs = container.querySelectorAll("select[name], input[name]");
      inputs.forEach(function (el) {
        elements.push(el);
      });
    });
    return elements;
  }

  /**
   * Read current filter values and persist to sessionStorage.
   */
  function saveFilters() {
    var elements = getFilterElements();
    if (elements.length === 0) return;

    var state = {};
    var hasValue = false;

    elements.forEach(function (el) {
      var name = el.getAttribute("name");
      if (!name) return;
      var value = el.value;
      if (value) {
        state[name] = value;
        hasValue = true;
      }
    });

    var key = getStorageKey();
    if (hasValue) {
      try {
        sessionStorage.setItem(key, JSON.stringify(state));
      } catch (e) {
        // sessionStorage full or unavailable — silently ignore
      }
    } else {
      // All filters at default — remove stored state
      sessionStorage.removeItem(key);
    }
  }

  /**
   * Restore saved filter values and trigger HTMX reload.
   * Only runs when the URL has no query parameters.
   */
  function restoreFilters() {
    // If URL has query parameters, the user navigated with explicit filters —
    // those take precedence, so we do NOT restore from storage.
    if (window.location.search) return;

    var key = getStorageKey();
    var raw;
    try {
      raw = sessionStorage.getItem(key);
    } catch (e) {
      return;
    }
    if (!raw) return;

    var state;
    try {
      state = JSON.parse(raw);
    } catch (e) {
      sessionStorage.removeItem(key);
      return;
    }

    var elements = getFilterElements();
    if (elements.length === 0) return;

    // Track whether we actually changed anything
    var changed = false;

    elements.forEach(function (el) {
      var name = el.getAttribute("name");
      if (!name || !(name in state)) return;
      if (el.value !== state[name]) {
        el.value = state[name];
        changed = true;
      }
    });

    if (!changed) return;

    // Trigger HTMX request on the last filter element that has hx-get,
    // so the content area reloads with the restored filter values.
    // We need a short delay to ensure HTMX is fully initialised.
    setTimeout(function () {
      // Find an element with hx-get to trigger
      var triggerEl = null;
      for (var i = 0; i < elements.length; i++) {
        if (elements[i].hasAttribute("hx-get")) {
          triggerEl = elements[i];
          break;
        }
      }
      if (triggerEl && typeof htmx !== "undefined") {
        htmx.trigger(triggerEl, "change");
      }
    }, 50);
  }

  /**
   * Attach change listeners to filter elements.
   */
  function attachListeners() {
    var elements = getFilterElements();
    elements.forEach(function (el) {
      el.addEventListener("change", saveFilters);
      // For text inputs, also listen to input event (debounced save)
      if (el.tagName === "INPUT") {
        var timer;
        el.addEventListener("input", function () {
          clearTimeout(timer);
          timer = setTimeout(saveFilters, 300);
        });
      }
    });
  }

  // Initialise on DOMContentLoaded
  document.addEventListener("DOMContentLoaded", function () {
    // Only act if there are filter-persist containers on this page
    if (document.querySelectorAll("[data-filter-persist]").length === 0) return;

    attachListeners();
    restoreFilters();
  });
})();
