/*
 * Time-Filter-Tab-Highlighting (Refs #692).
 *
 * Ersetzt die fruehere Inline-Loesung ueber ``hx-on::before-request`` in
 * ``components/_time_filter_dropdown.html``. Inline-HTMX-Handler werden
 * intern per ``Function()`` evaluiert und brauchen ``script-src 'unsafe-eval'``,
 * was unter dem strengen CSP nach #690 nicht mehr erlaubt ist.
 *
 * Der Listener filtert global auf ``htmx:beforeRequest`` und reagiert nur,
 * wenn das Quell-Element die ``.time-filter-tab``-Klasse hat. Aktive
 * Stilklassen werden dabei vom vorherigen aktiven Tab entfernt und auf den
 * gerade angeklickten Tab gesetzt.
 *
 * Refs #1059 (A11Y-017/008, R-B2p-10): Die Leiste selbst wird beim
 * Umschalten NICHT neu gerendert (der Swap trifft die Feed-Liste). Was hier
 * nicht mitgezogen wird, bleibt also auf dem Server-Stand stehen - deshalb
 * wandern ``aria-pressed`` (der Auswahlzustand war vorher rein visuell) und
 * die Textfarbe der Uhrzeit-Spanne (``text-ink-muted`` erreicht auf
 * ``bg-accent-light`` nur 4,31:1) zusammen mit den Stilklassen.
 *
 * Bewusst ``aria-pressed`` statt ``aria-selected``/``role="tab"``: die
 * Knoepfe sind unabhaengige Umschalter, kein ARIA-Tab-Widget (kein
 * Roving-Tabindex, keine Pfeilnavigation, kein zugeordnetes ``tabpanel``).
 */
(function () {
    "use strict";

    var ACTIVE = [
        "bg-accent-light",
        "text-accent",
        "border-b-2",
        "border-accent",
        "font-semibold",
    ];
    var INACTIVE = ["text-ink-muted", "hover:text-ink", "hover:bg-canvas"];
    // Uhrzeit-Spanne im Tab (nur die benannten Zeitfilter haben eine).
    var TIME_SPAN = ".time-filter-tab-time";
    var TIME_ACTIVE = "text-accent";
    var TIME_INACTIVE = "text-ink-muted";

    function _setTimeSpanState(tab, active) {
        var span = tab.querySelector(TIME_SPAN);
        if (!span) {
            return;
        }
        span.classList.remove(active ? TIME_INACTIVE : TIME_ACTIVE);
        span.classList.add(active ? TIME_ACTIVE : TIME_INACTIVE);
    }

    document.body.addEventListener("htmx:beforeRequest", function (event) {
        var target = event.target;
        if (!target || !target.classList || !target.classList.contains("time-filter-tab")) {
            return;
        }
        document.querySelectorAll(".time-filter-tab").forEach(function (b) {
            ACTIVE.forEach(function (cls) {
                b.classList.remove(cls);
            });
            INACTIVE.forEach(function (cls) {
                b.classList.add(cls);
            });
            b.setAttribute("aria-pressed", "false");
            _setTimeSpanState(b, false);
        });
        INACTIVE.forEach(function (cls) {
            target.classList.remove(cls);
        });
        ACTIVE.forEach(function (cls) {
            target.classList.add(cls);
        });
        target.setAttribute("aria-pressed", "true");
        _setTimeSpanState(target, true);
    });
})();
