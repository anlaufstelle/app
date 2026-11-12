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

    // Countdown + expiry of the undo bar. The bar shows the remaining seconds in an
    // aria-hidden suffix, so assistive technology hears only the static label.
    function scheduleExpiry(root) {
        root.querySelectorAll("[data-tally-undo-expires-at]").forEach(function (form) {
            if (form.dataset.tallyUndoBound) return;
            var expiresAt = Date.parse(form.dataset.tallyUndoExpiresAt || "");
            if (Number.isNaN(expiresAt)) return;
            form.dataset.tallyUndoBound = "1";
            var label = form.querySelector("[data-tally-undo-remaining]");
            var timer = null;
            var remove = function () {
                if (timer !== null) window.clearInterval(timer);
                var bar = form.closest("[data-tally-undo-bar]") || form;
                bar.remove();
            };
            var tick = function () {
                if (!form.isConnected) {
                    // Replaced by a newer tap or emptied by an undo: stop quietly.
                    window.clearInterval(timer);
                    return;
                }
                var remaining = expiresAt - Date.now();
                if (remaining <= 0) {
                    remove();
                    return;
                }
                if (label) label.textContent = String(Math.ceil(remaining / 1000));
            };
            if (expiresAt - Date.now() <= 0) {
                remove();
                return;
            }
            timer = window.setInterval(tick, 1000);
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
    function onSwap() {
        // The swapped-in tile replaces the old target and the undo bar arrives
        // out of band, so look at the whole page.
        scheduleExpiry(document);
        pulseFreshTiles(document);
    }
    document.body.addEventListener("htmx:afterSwap", onSwap);
    document.body.addEventListener("htmx:oobAfterSwap", onSwap);
})();
