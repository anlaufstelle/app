/* Strichlisten-Skript: Farbpuls der Kachel und kurzlebige Ruecknahme.
 *
 * Farbpuls: Die servergerenderte Kachel eines frischen Strichs traegt
 * data-tally-pulse. Nach dem Swap bekommt sie fuer ~450 ms Hintergrund und
 * Rahmen der Akzentfarbe; das Attribut wird verbraucht, damit spaetere Swaps
 * nicht erneut pulsen. Klassenwechsel statt Inline-Style (CSP).
 *
 * Kurzlebige Tally-Ruecknahme nur als UX ausblenden (Refs #1913).
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

    var PULSE_MS = 450;

    function pulseFreshTiles(root) {
        root.querySelectorAll("[data-tally-pulse]").forEach(function (tile) {
            tile.removeAttribute("data-tally-pulse");
            tile.classList.add("bg-accent-light", "border-accent");
            window.setTimeout(function () {
                tile.classList.remove("bg-accent-light", "border-accent");
            }, PULSE_MS);
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        scheduleExpiry(document);
        pulseFreshTiles(document);
    });
    document.body.addEventListener("htmx:afterSwap", function (event) {
        scheduleExpiry(event.target);
        // The swapped-in tile replaces the old target, so look at the whole page.
        pulseFreshTiles(document);
    });
})();
