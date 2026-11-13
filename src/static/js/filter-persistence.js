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

  // Refs #1804 (Konzept §3.6, „Migrationsfalle"): Umbenannte Filterwerte, die
  // in bereits ausgelieferten Sitzungen im sessionStorage stehen können. Der
  // Server akzeptiert die Altwerte dauerhaft als stille Aliasse (Entscheid F5);
  // hier werden sie zusätzlich beim Lesen migriert, damit der In-Place-Restore
  // (`restoreInPlace`, Listen ohne `data-filter-persist-url`) keinen Wert in ein
  // Select schreibt, das diese Option nicht mehr führt — dort fiele die Auswahl
  // stumm auf den leeren Wert zurück, während die Liste gefiltert bliebe.
  var LEGACY_VALUES = {
    due: { week: "next7" },
  };

  function migrateLegacyValues(state) {
    var changed = false;
    Object.keys(LEGACY_VALUES).forEach(function (name) {
      var current = state[name];
      var replacement = current && LEGACY_VALUES[name][current];
      if (replacement) {
        state[name] = replacement;
        changed = true;
      }
    });
    return changed;
  }

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
    // Refs #2660: logout and "quick exit" empty the tab's sessionStorage and
    // set this flag. The old document may live on for a moment (late input,
    // slow logout), but must not store filter values again.
    if (window.__tabStorageWiped) return;

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

    // Einmalige Migration umbenannter Werte, sofort zurückgeschrieben — sonst
    // liefe sie bei jedem Einstieg erneut über denselben Altwert.
    if (migrateLegacyValues(state)) {
      try {
        sessionStorage.setItem(key, JSON.stringify(state));
      } catch (e) {
        // sessionStorage voll/nicht verfügbar — der Server versteht den
        // Altwert ohnehin weiterhin.
      }
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
   * Persist on change/input. Delegated on the document: forms that are replaced
   * out of band (contacts list) keep saving without re-attaching listeners.
   */
  var inputTimer;
  function inFilterContainer(event) {
    var target = event.target;
    return !!(target && target.closest && target.closest("[data-filter-persist]"));
  }

  function attachListeners() {
    if (attachListeners.done) return;
    attachListeners.done = true;
    document.addEventListener("change", function (event) {
      if (inFilterContainer(event)) saveFilters();
    });
    // For text inputs, also listen to input event (debounced save)
    document.addEventListener("input", function (event) {
      if (!inFilterContainer(event) || event.target.tagName !== "INPUT") return;
      clearTimeout(inputTimer);
      inputTimer = setTimeout(saveFilters, 300);
    });
  }

  // ---- Filter panel of the contacts list (details[data-filter-panel]) ----

  var PANEL = "details[data-filter-panel]";
  var PANEL_BODY = "[data-filter-panel-body]";

  function panelToggle(panel) {
    return panel.querySelector("summary");
  }

  function firstPanelField(panel) {
    var body = panel.querySelector(PANEL_BODY);
    if (!body) return null;
    return (
      body.querySelector('input:not([type="hidden"]):not([disabled]), select:not([disabled])') ||
      body.querySelector("a[href], button:not([disabled])")
    );
  }

  function closePanel(panel, returnFocus) {
    panel.open = false;
    if (returnFocus) {
      var toggle = panelToggle(panel);
      if (toggle) toggle.focus();
    }
  }

  // Focus the first field on open, unless focus is already inside (e.g. after
  // an out-of-band swap re-created the open panel around the active control).
  document.addEventListener(
    "toggle",
    function (event) {
      var panel = event.target;
      if (!panel || !panel.matches || !panel.matches(PANEL) || !panel.open) return;
      var body = panel.querySelector(PANEL_BODY);
      if (body && body.contains(document.activeElement)) return;
      var field = firstPanelField(panel);
      if (field) field.focus();
    },
    true
  );

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    var target = event.target;
    var panel = target && target.closest ? target.closest(PANEL) : null;
    if (!panel || !panel.open) return;
    event.preventDefault();
    closePanel(panel, true);
  });

  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target || !target.closest) return;
    // "Zeitraum" without a preset has no request: it opens the date fields.
    var custom = target.closest('a[data-period-segment][data-period="custom"]');
    if (custom && !custom.hasAttribute("hx-get")) {
      var owner = custom.closest("form") || document;
      var own = owner.querySelector(PANEL);
      if (own) {
        event.preventDefault();
        own.open = true;
        var field = firstPanelField(own);
        if (field) field.focus();
        return;
      }
    }
    document.querySelectorAll(PANEL + "[open]").forEach(function (panel) {
      if (!panel.contains(target)) closePanel(panel, false);
    });
  });

  // JavaScript-only: the "Filtern" fallback is superfluous once htmx filters
  // immediately. Without htmx it stays, so the form remains usable.
  function hideSubmitFallbacks(root) {
    if (typeof htmx === "undefined" || !htmx) return;
    root.querySelectorAll("[data-filter-submit-fallback]").forEach(function (button) {
      button.hidden = true;
    });
  }

  // The form is re-rendered out of band after every response. Carry over what
  // the user is doing in the old form: the open panel and the text being typed.
  document.addEventListener("htmx:oobBeforeSwap", function (event) {
    var detail = event.detail || {};
    var old = detail.target;
    var fragment = detail.fragment;
    if (!old || !fragment || !old.matches || !old.matches("form[data-filter-persist]")) return;
    hideSubmitFallbacks(fragment);
    var oldPanel = old.querySelector(PANEL);
    var newPanel = fragment.querySelector(PANEL);
    if (oldPanel && oldPanel.open && newPanel) newPanel.open = true;
    var active = document.activeElement;
    if (active && active.id && active.tagName === "INPUT" && old.contains(active)) {
      var incoming = fragment.querySelector("#" + CSS.escape(active.id));
      if (incoming && incoming.type !== "hidden") incoming.value = active.value;
    }
  });

  // A swap changes the state (preset, chips) without a change event: store it.
  document.addEventListener("htmx:oobAfterSwap", function (event) {
    var target = event.target;
    if (target && target.matches && target.matches("form[data-filter-persist]")) saveFilters();
  });

  // Reset-Link-Delegation global registrieren (Refs #1611) — greift auch,
  // wenn der Link erst per HTMX-Swap in den DOM kommt.
  document.addEventListener("click", handleResetClick);

  // Initialise on DOMContentLoaded
  document.addEventListener("DOMContentLoaded", function () {
    // Only act if there are filter-persist containers on this page
    if (document.querySelectorAll("[data-filter-persist]").length === 0) return;

    attachListeners();
    hideSubmitFallbacks(document);
    restoreFilters();
  });
})();
