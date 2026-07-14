/*
 * Globales Fokus-Management nach HTMX-Partial-Swaps (Refs #1339).
 *
 * Ohne diesen Handler faellt der Tastatur-/Screenreader-Fokus nach JEDEM
 * hx-Swap (Statusbuttons in components/_workitem_row.html, Filter,
 * Formular-Re-Renders) auf <body> zurueck: der getauschte Inhalt bleibt fuer
 * Screenreader stumm, Tab-Nutzer verlieren ihre Position. Dieser Handler setzt
 * nach dem Swap den Fokus auf ein sinnvolles Ziel INNERHALB des getauschten
 * Fragments — damit assistive Technik den neuen Inhalt ansagt und die
 * Tab-Reihenfolge nahtlos dort weiterlaeuft.
 *
 * Reine DOM-Manipulation, CSP-konform (kein Inline-Script, kein eval) — Muster
 * wie der globale Body-Listener in htmx-errors.js (Refs #1016, C2).
 *
 * Bewusst KEIN systematisches WCAG-Audit / axe-core (das bliebe M3/#1059 unter
 *-Sperre); dies ist der non-gated Fokus-Quick-Fix (Milestone release-0.22).
 *
 * Abgrenzung Live-Suche: Bei keyup-getriggerten Live-Such-/Filterfeldern (z.B.
 * core/search/index.html, core/clients/list.html) darf der Fokus NICHT ins
 * Ergebnis springen — sonst tippt der Nutzer ins Leere. Solche Swaps werden
 * uebersprungen, weil ihr Ausloeser ein noch fokussiertes Texteingabefeld ist
 * (bleibt ausserhalb des Swap-Ziels erhalten). Zusaetzlich existiert das
 * explizite Opt-out-Attribut data-hx-no-focus fuer Sonderfaelle.
 */
(function () {
    "use strict";

    // Explizites Opt-out: am Swap-Ziel ODER am aktuell fokussierten Element
    // (bzw. einem ihrer Vorfahren) gesetzt, unterbindet das Fokus-Setzen fuer
    // diesen Swap komplett.
    var OPT_OUT_ATTR = "data-hx-no-focus";
    // Explizites Opt-in: markiert im getauschten Fragment das gewuenschte
    // Fokus-Ziel (hat Vorrang vor der generischen Heuristik).
    var OPT_IN_SELECTOR = "[autofocus], [data-hx-focus]";

    // Texteingabe-Feldtypen, deren Fokus bei Live-Suche/Filter NICHT gestohlen
    // werden darf. <select> zaehlt bewusst NICHT dazu: nach einer Auswahl ist es
    // legitim, den Fokus in das gefilterte Ergebnis zu verschieben.
    var TEXT_INPUT_TYPES = ["text", "search", "email", "tel", "url", "number", "password"];

    function _isTextEntry(el) {
        if (!el || el.nodeType !== 1) {
            return false;
        }
        if (el.isContentEditable === true) {
            return true;
        }
        var tag = el.tagName;
        if (tag === "TEXTAREA") {
            return true;
        }
        if (tag === "INPUT") {
            var type = (el.getAttribute("type") || "text").toLowerCase();
            return TEXT_INPUT_TYPES.indexOf(type) !== -1;
        }
        return false;
    }

    // Waehlt das Fokus-Ziel innerhalb des getauschten Containers:
    // (a) erstes explizit markiertes Element ([autofocus]/[data-hx-focus]),
    // (b) sonst der Container selbst (per tabindex=-1 programmatisch fokussierbar
    //     gemacht) — assistive Technik sagt seinen Inhalt an, Tab laeuft weiter.
    function _resolveTarget(container) {
        var explicit = container.querySelector(OPT_IN_SELECTOR);
        if (explicit) {
            return explicit;
        }
        return container;
    }

    function _makeFocusable(el) {
        // Interaktive/bereits fokussierbare Elemente unangetastet lassen. Nur
        // generische Container brauchen ein tabindex=-1, um .focus() anzunehmen
        // (haelt sie zugleich aus der normalen Tab-Reihenfolge heraus).
        if (!el.hasAttribute("tabindex")) {
            el.setAttribute("tabindex", "-1");
        }
    }

    // Nach einem outerHTML-Swap ist event.detail.target das ERSETZTE (und aus
    // dem DOM geloeste) Element. Der Fokus muss auf das neu eingefuegte Pendant
    // wandern — bei htmx traegt es dieselbe id. Ist das urspruengliche Ziel noch
    // verbunden (innerHTML-Swap), bleibt es unveraendert.
    function _resolveConnectedContainer(target) {
        if (target.isConnected) {
            return target;
        }
        if (target.id) {
            var replacement = document.getElementById(target.id);
            if (replacement) {
                return replacement;
            }
        }
        return null;
    }

    function _handleAfterSwap(event) {
        var detail = event && event.detail ? event.detail : {};
        var target = _resolveConnectedContainer(detail.target || {});

        if (!target || target.nodeType !== 1) {
            return;
        }

        var active = document.activeElement;

        // Explizites Opt-out am Swap-Ziel oder am aktuell fokussierten Element.
        if (target.closest && target.closest("[" + OPT_OUT_ATTR + "]")) {
            return;
        }
        if (active && active.closest && active.closest("[" + OPT_OUT_ATTR + "]")) {
            return;
        }

        // War der Fokus vor dem Swap bereits INNERHALB des getauschten Ziels
        // (z.B. htmx hat ihn auf ein Element mit stabiler id restauriert), ist
        // alles gut — nicht dazwischenfunken.
        if (active && active !== document.body && target.contains(active)) {
            return;
        }

        // Live-Suche/Filter: Der Fokus liegt in einem Texteingabefeld AUSSERHALB
        // des Swap-Ziels (keyup-getriggerte Live-Suche haelt den Fokus im Feld).
        // Fokus NICHT stehlen — sonst tippt der Nutzer ins Leere. Bewusst am
        // aktiven Element geprueft (nicht an detail.elt): htmx setzt ``elt`` bei
        // afterSwap auf das Swap-Ziel, nicht auf das ausloesende Eingabefeld.
        if (_isTextEntry(active) && !target.contains(active)) {
            return;
        }

        var focusTarget = _resolveTarget(target);
        if (!focusTarget) {
            return;
        }
        _makeFocusable(focusTarget);
        try {
            focusTarget.focus({ preventScroll: false });
        } catch (_e) {
            // Fokus kann in Randfaellen scheitern (Element nicht sichtbar o.ae.)
            // — dann bleibt der Ausgangszustand, kein harter Fehler.
        }
    }

    // htmx:afterSwap feuert direkt nach dem DOM-Swap und bubbelt zum body
    // (Muster wie htmx-errors.js). Bewusst afterSwap statt afterSettle: der
    // Fokus soll sitzen, BEVOR eine evtl. Alpine-Reinitialisierung des Fragments
    // dazwischengrätscht.
    document.body.addEventListener("htmx:afterSwap", _handleAfterSwap);
})();
