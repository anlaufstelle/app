/*
 * Generic confirmation handler (Refs #867 / #662 FND-01).
 *
 * Forms with ``data-confirm="..."`` show ``window.confirm`` before submit;
 * elements (e.g. buttons) with ``data-action="print"`` run ``window.print``.
 * Inline ``onclick=`` / ``onsubmit=`` would be CSP-blocked.
 */
(function () {
    "use strict";

    document.addEventListener(
        "submit",
        function (event) {
            var form = event.target;
            if (!(form instanceof HTMLFormElement)) return;
            var msg = form.getAttribute("data-confirm");
            if (msg && !window.confirm(msg)) {
                event.preventDefault();
            }
        },
        true
    );

    document.addEventListener(
        "click",
        function (event) {
            var el = event.target.closest("[data-action]");
            if (!el) return;
            if (el.getAttribute("data-action") === "print") {
                event.preventDefault();
                window.print();
            }
        },
        true
    );
})();
