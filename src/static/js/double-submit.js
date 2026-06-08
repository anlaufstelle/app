/*
 * Doppel-Submit-Schutz für Standard-Formulare (Refs #1016, Workstream C — C3).
 *
 * Verhindert doppelte POSTs durch Doppelklick / doppeltes Enter. Greift NUR bei
 * normalen (nicht-HTMX) Formularen — HTMX verwaltet seinen Request-Lifecycle
 * selbst. CSP-konform (externes Script, kein Inline-Handler), Capture-Phase wie
 * confirm-action.js, das vorher läuft und ggf. abbricht (defaultPrevented).
 *
 * WICHTIG — Reihenfolge: Der aktivierte Submit-Button wird NICHT synchron im
 * submit-Handler disabled. Ein disabled-Control wird nicht serialisiert; sein
 * name/value (z.B. action=approve/reject in deletion_review.html oder
 * language=de/en im Sprachumschalter) würde sonst aus den POST-Daten fallen.
 * Erst nach einem Tick (setTimeout 0), wenn die Formulardaten bereits gebildet
 * sind, blenden wir die Buttons aus. Der eigentliche Schutz ist die Flag-Prüfung
 * (data-submitting) — sie blockt den 2. Submit unabhängig vom Button-State.
 */
(function () {
    "use strict";

    var HX_ATTRS = ["hx-post", "hx-get", "hx-put", "hx-patch", "hx-delete"];

    function _isHtmxForm(form) {
        for (var i = 0; i < HX_ATTRS.length; i++) {
            if (form.hasAttribute(HX_ATTRS[i])) return true;
        }
        return false;
    }

    document.addEventListener(
        "submit",
        function (event) {
            if (event.defaultPrevented) return; // z.B. confirm-action hat abgebrochen
            var form = event.target;
            if (!(form instanceof HTMLFormElement)) return;
            if (_isHtmxForm(form)) return;

            if (form.dataset.submitting === "1") {
                event.preventDefault(); // läuft bereits → 2. Submit blocken
                return;
            }
            form.dataset.submitting = "1";

            // Erst nach der Serialisierung disablen, sonst geht der Button-name
            // verloren. Rein visuelles Feedback; der Schutz greift schon oben.
            window.setTimeout(function () {
                var buttons = form.querySelectorAll(
                    'button[type="submit"], input[type="submit"], button:not([type])'
                );
                buttons.forEach(function (btn) {
                    btn.disabled = true;
                    btn.dataset.reenableOnShow = "1";
                });
            }, 0);
        },
        true
    );

    // Back-/Forward-Cache: Beim Zurücknavigieren stellt der Browser die Seite
    // aus dem bfcache wieder her — ohne Reset bliebe das Formular gesperrt.
    window.addEventListener("pageshow", function () {
        document.querySelectorAll('[data-reenable-on-show="1"]').forEach(function (btn) {
            btn.disabled = false;
            delete btn.dataset.reenableOnShow;
        });
        document.querySelectorAll('form[data-submitting="1"]').forEach(function (form) {
            form.dataset.submitting = "0";
        });
    });
})();
