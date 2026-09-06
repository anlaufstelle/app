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
 * Historie: entstanden als non-gated Fokus-Quick-Fix (Milestone release-0.22)
 * ohne systematisches WCAG-Audit. Die WCAG-2.1-AA-Welle #1059 hat die
 * Zielwahl seither um Stufe 2 (erstes invalides Feld) ergaenzt.
 *
 * Zielwahl (drei Stufen, Refs #1059 — die im Ticket beschriebene
 * "3-Stufen-Strategie"):
 *   1. Opt-in-Element [autofocus]/[data-hx-focus] im getauschten Fragment,
 *   2. sonst das ERSTE als aria-invalid="true" markierte Feld — nach einem
 *      Server-Re-Render mit Validierungsfehlern landet der Fokus damit auf
 *      dem Feld, das den Fehler traegt (WCAG 3.3.1: der zugehoerige
 *      <ul id="…-error" role="alert"> haengt per aria-describedby daran und
 *      wird mitgelesen),
 *   3. sonst der Swap-Container selbst (nur HIER wird tabindex=-1 ergaenzt,
 *      damit ein generischer <div> .focus() annimmt) — assistive Technik
 *      sagt seinen Inhalt an, Tab laeuft nahtlos weiter.
 * Opt-out und Live-Such-Schutz stehen VOR dieser Wahl und bleiben
 * unveraendert.
 *
 * Trefferansage (Refs #1059, A11Y-035/036): Listen, die per HTMX gefiltert
 * werden, tauschen ihre Tabelle stumm aus — die neue Trefferzahl erreicht
 * assistive Technik nie. Traegt der Swap-Container die
 * ``data-hx-announce-*``-Attribute, schreibt dieser Handler den Satz in die
 * dauerhaft im DOM stehende ``role="status"``-Zeile, die das Listen-Template
 * NEBEN dem Swap-Ziel haelt (eine Live-Region, die mit ihrem Inhalt zugleich
 * eingefuegt wird, sagt in NVDA/JAWS nichts an). Die Ansage laeuft VOR den
 * Fokus-Ausstiegen: sie ist von ``data-hx-no-focus`` unabhaengig — Listen
 * setzen genau dieses Opt-out, damit der Container nicht zusaetzlich
 * fokussiert und dadurch ein zweites Mal vorgelesen wird.
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
    // Stufe 2 (Refs #1059): das erste Feld, das der Server als fehlerhaft
    // zurueckgerendert hat. ``{% aria_field %}``/``components/_form_field.html``
    // setzen genau dieses Attribut am Widget, nicht am Wrapper.
    var INVALID_SELECTOR = '[aria-invalid="true"]';

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

    // Sucht einen Treffer im Container ODER am Container selbst. Bei einem
    // outerHTML-Swap ist der Container haeufig das getauschte Feld-Fragment,
    // dann traegt er das Attribut selbst und ein reines querySelector (das
    // nur Nachfahren sieht) uebersaehe ihn.
    function _findWithin(container, selector) {
        if (container.matches && container.matches(selector)) {
            return container;
        }
        return container.querySelector(selector);
    }

    // Waehlt ein AUSGEZEICHNETES Fokus-Ziel innerhalb des getauschten Containers:
    // (a) erstes explizit markiertes Element ([autofocus]/[data-hx-focus]),
    // (b) sonst das erste Feld mit aria-invalid="true" (Server-Re-Render mit
    //     Validierungsfehlern — der Fokus soll auf den Fehler zeigen).
    // Findet sich keines, gibt die Funktion null zurueck und der Aufrufer faellt
    // auf den Container selbst zurueck (Stufe 3).
    function _resolveTarget(container) {
        var explicit = _findWithin(container, OPT_IN_SELECTOR);
        if (explicit) {
            return explicit;
        }
        return _findWithin(container, INVALID_SELECTOR);
    }

    function _makeFocusable(el) {
        // NUR fuer den Container-Fallback (Stufe 3): ein generischer <div>
        // nimmt ohne tabindex kein .focus() an; -1 haelt ihn zugleich aus der
        // normalen Tab-Reihenfolge heraus.
        //
        // Refs #1059 (Cross-Review B2', HOCH): Auf die Stufen 1 und 2 darf das
        // NICHT angewandt werden. Deren Ziele sind ausgezeichnete Bedienelemente
        // — nativ fokussierbar und Teil der Tab-Reihenfolge. Ein hier gesetztes
        // tabindex="-1" haette das gerade als fehlerhaft angesagte Feld aus der
        // Vorwaerts-/Rueckwaerts-Navigation entfernt: einmal verlassen, waere es
        // per Tastatur nicht wieder erreichbar gewesen.
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

    // --- Trefferansage (Refs #1059) -----------------------------------------
    // Deklarativ ueber Attribute am Swap-Container, damit jede Liste sie ohne
    // eigenes Skript bekommt:
    //   data-hx-announce-region  id der dauerhaften role="status"-Zeile
    //   data-hx-announce-rows    Selektor der zu zaehlenden Zeilen
    //   data-hx-announce-none    Text bei 0 Treffern
    //   data-hx-announce-one     Text bei genau 1 Treffer
    //   data-hx-announce-many    Text ab 2 Treffern, ``{count}`` als Platzhalter
    // Die Texte kommen uebersetzt aus dem Template (Muster
    // ``data-confirm-remove-text`` in core/clients/list.html) — hier steht
    // kein deutscher String im Skript.
    function _announceResultCount(container) {
        var regionId = container.getAttribute
            ? container.getAttribute("data-hx-announce-region")
            : null;
        if (!regionId) {
            return;
        }
        var region = document.getElementById(regionId);
        var rowSelector = container.getAttribute("data-hx-announce-rows");
        if (!region || !rowSelector) {
            return;
        }
        var count = container.querySelectorAll(rowSelector).length;
        var template;
        if (count === 0) {
            template = container.getAttribute("data-hx-announce-none");
        } else if (count === 1) {
            template = container.getAttribute("data-hx-announce-one");
        } else {
            template = container.getAttribute("data-hx-announce-many");
        }
        if (!template) {
            return;
        }
        // textContent zu setzen ersetzt den Textknoten auch bei gleichem Text —
        // die Mutation ist damit auch dann beobachtbar, wenn ein Filterwechsel
        // dieselbe Trefferzahl liefert.
        region.textContent = template.replace("{count}", String(count));
    }

    function _handleAfterSwap(event) {
        var detail = event && event.detail ? event.detail : {};
        var target = _resolveConnectedContainer(detail.target || {});

        if (!target || target.nodeType !== 1) {
            return;
        }

        // Vor jeder Fokus-Entscheidung: die Trefferansage haengt an der
        // Liste, nicht am Fokus, und muss auch unter data-hx-no-focus laufen.
        _announceResultCount(target);

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
            // Stufe 3: der Container selbst — und NUR er bekommt tabindex=-1.
            focusTarget = target;
            _makeFocusable(focusTarget);
        }
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
