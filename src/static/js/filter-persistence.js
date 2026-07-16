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
   *
   * Refs #787: Felder, die Klartext-Suchbegriffe (Pseudonyme, Notizen)
   * aufnehmen, werden ausgeschlossen. Mechanik:
   *   1. ``data-filter-persist-exclude``-Attribut auf dem Input.
   *   2. ``name="q"``-Konvention — Suchfelder werden niemals persistiert.
   * Kategoriale Filter (Stage, Age, Status) bleiben weiterhin erhalten.
   */
  function getFilterElements() {
    var containers = document.querySelectorAll("[data-filter-persist]");
    var elements = [];
    containers.forEach(function (container) {
      var inputs = container.querySelectorAll("select[name], input[name]");
      inputs.forEach(function (el) {
        if (el.hasAttribute("data-filter-persist-exclude")) return;
        if (el.getAttribute("name") === "q") return;
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
   * Restore persisted filters by redirecting to the persisted querystring.
   *
   * Refs #1611: Ersetzt den früheren "Set-Values + künstlicher change-Trigger"
   * für Listen, die sich per ``[data-filter-persist-url]`` dafür entscheiden
   * (aktuell die Aufgaben-Inbox). Statt die volle Liste ungefiltert zu rendern
   * und ~50 ms später per HTMX-Swap gefiltert zu ersetzen (sichtbarer Flash),
   * navigiert ein einzelner ``location.replace`` direkt zur gefilterten URL —
   * der Server rendert sofort korrekt, die Sicht ist reload-/teilbar.
   *
   * ``replace`` (kein ``assign``): der query-lose Einstiegs-Eintrag bleibt aus
   * der History (kein zusätzlicher Zurück-Schritt), und es entsteht keine
   * Redirect-Schleife — die neu geladene URL trägt einen Querystring, sodass
   * ``restoreFilters`` beim nächsten Lauf sofort früh aussteigt.
   */
  function restoreViaUrl(elements, state) {
    var knownNames = {};
    elements.forEach(function (el) {
      var name = el.getAttribute("name");
      if (name) knownNames[name] = true;
    });

    var params = new URLSearchParams();
    var hasValue = false;
    Object.keys(state).forEach(function (name) {
      // Nur Filter übernehmen, die auf dieser Seite tatsächlich existieren —
      // stale Keys aus anderen Ansichten überspringen.
      if (!knownNames[name]) return;
      var value = state[name];
      if (value) {
        params.set(name, value);
        hasValue = true;
      }
    });

    if (!hasValue) return;
    window.location.replace(window.location.pathname + "?" + params.toString());
  }

  /**
   * Set persisted filter values in-place and trigger an HTMX reload.
   *
   * Legacy-Pfad für Listen ohne ``[data-filter-persist-url]`` (z.B. Klientel,
   * Zeitstrom): Werte setzen und einmal ``change`` feuern.
   */
  function restoreInPlace(elements, state) {
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
   * Restore saved filter values on page load.
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

    // Refs #1611: Views mit [data-filter-persist-url] heben den Filter-State in
    // die URL (voller, gefilterter Server-Render ohne Flash). Alle übrigen
    // Listen behalten den bisherigen In-Place-Restore.
    var urlRestore =
      document.querySelector("[data-filter-persist][data-filter-persist-url]") !== null;

    if (urlRestore) {
      restoreViaUrl(elements, state);
    } else {
      restoreInPlace(elements, state);
    }
  }

  /**
   * Refs #1611: Reset-Links im filterbewussten Empty-State ("Filter
   * zurücksetzen") löschen den persistierten Filter-State, damit die
   * Rücksetzung dauerhaft ist und der URL-Restore beim nächsten Einstieg ohne
   * Querystring nicht erneut die alten Filter herstellt. Delegiert auf
   * ``document``, weil der Link Teil des per HTMX getauschten Inhalts ist. Die
   * eigentliche Navigation überlässt der Handler dem ``href`` (kein
   * ``preventDefault``) — ``removeItem`` läuft synchron davor.
   */
  function handleResetClick(e) {
    var target = e.target;
    if (!target || !target.closest) return;
    if (!target.closest("[data-filter-reset]")) return;
    try {
      sessionStorage.removeItem(getStorageKey());
    } catch (err) {
      // sessionStorage unavailable — nothing to clear.
    }
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

  // Reset-Link-Delegation global registrieren (Refs #1611) — greift auch,
  // wenn der Link erst per HTMX-Swap in den DOM kommt.
  document.addEventListener("click", handleResetClick);

  // Initialise on DOMContentLoaded
  document.addEventListener("DOMContentLoaded", function () {
    // Only act if there are filter-persist containers on this page
    if (document.querySelectorAll("[data-filter-persist]").length === 0) return;

    attachListeners();
    restoreFilters();
  });
})();
