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
        });
        INACTIVE.forEach(function (cls) {
            target.classList.remove(cls);
        });
        ACTIVE.forEach(function (cls) {
            target.classList.add(cls);
        });
    });
})();
