/* Kurzlebige Tally-Ruecknahme nur als UX ausblenden (Refs #1913).
 *
 * Die serverseitige TimestampSigner- und Event-Zeitpruefung bleibt die
 * Sicherheitsgrenze. Dieses externe, CSP-konforme Skript verhindert nur, dass
 * ein abgelaufener Button weiterhin wie eine moegliche Aktion aussieht.
 */
(function () {
    "use strict";

    function scheduleExpiry(root) {
        root.querySelectorAll("[data-tally-undo-expires-at]").forEach(function (form) {
            var expiresAt = Date.parse(form.dataset.tallyUndoExpiresAt || "");
            if (Number.isNaN(expiresAt)) return;
            var remove = function () {
                form.remove();
            };
            var remaining = expiresAt - Date.now();
            if (remaining <= 0) {
                remove();
                return;
            }
            window.setTimeout(remove, remaining + 10);
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        scheduleExpiry(document);
    });
    document.body.addEventListener("htmx:afterSwap", function (event) {
        scheduleExpiry(event.target);
    });
})();
