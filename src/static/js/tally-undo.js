/* Tally script: tile colour pulse, number entry sheet and the short-lived undo.
 *
 * Colour pulse: the server-rendered tile of a fresh tally carries
 * data-tally-pulse. After the swap it gets the accent background and border for
 * ~450 ms; the attribute is consumed so later swaps do not pulse again. Class
 * toggling instead of inline styles (CSP).
 *
 * Number entry: the server-rendered (or offline-tally.js built)
 * <dialog data-tally-number-sheet> in the panel slot is opened here -- a
 * non-modal popover at the tile on desktop, a modal bottom sheet on small
 * screens (aria-modal only there). Escape, cancel, the close button and a click
 * outside close it; focus returns to the tile. Positioning goes through the CSS
 * object model (CSP-safe, no style attribute in the markup).
 *
 * Undo bar: countdown and expiry are UX only (Refs #1913). The server-side
 * TimestampSigner and event time checks stay the security boundary; this
 * external, CSP-safe script only keeps an expired button from looking like a
 * possible action.
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

    // ---- number entry sheet ------------------------------------------------
    var SHEET_SELECTOR = "[data-tally-number-sheet]";
    var current = null;

    function isDesktop() {
        return window.matchMedia ? window.matchMedia("(min-width: 768px)").matches : true;
    }

    function triggerFor(documentType) {
        return document.querySelector('[data-testid="tally-increment-' + documentType + '"]');
    }

    function focusTrigger(documentType) {
        var trigger = triggerFor(documentType);
        if (trigger && typeof trigger.focus === "function") trigger.focus();
    }

    function positionPopover(sheet, trigger) {
        var panel = sheet.closest("#tally-panel");
        var tile = trigger && trigger.closest('[data-testid^="tally-row-"]');
        if (!panel || !tile) return;
        var panelRect = panel.getBoundingClientRect();
        var tileRect = tile.getBoundingClientRect();
        var width = sheet.offsetWidth || 360;
        var left = Math.max(0, Math.min(tileRect.left - panelRect.left, Math.max(0, panelRect.width - width)));
        sheet.style.top = Math.round(tileRect.bottom - panelRect.top + 6) + "px";
        sheet.style.left = Math.round(left) + "px";
    }

    function firstFocusTarget(sheet) {
        return (
            sheet.querySelector('[aria-invalid="true"]') ||
            sheet.querySelector('input:not([type="hidden"]), select, textarea')
        );
    }

    function openSheet(sheet, trigger) {
        var documentType = sheet.dataset.documentType || "";
        sheet.dataset.tallySheetBound = "1";
        current = { sheet: sheet, documentType: documentType };
        // Without JavaScript the dialog is visible through its open attribute;
        // reopen it the proper way.
        sheet.removeAttribute("open");
        if (isDesktop()) {
            if (typeof sheet.show === "function") sheet.show();
            else sheet.setAttribute("open", "");
            positionPopover(sheet, trigger || triggerFor(documentType));
        } else {
            if (typeof sheet.showModal === "function") sheet.showModal();
            else sheet.setAttribute("open", "");
            sheet.setAttribute("aria-modal", "true");
        }
        var target = firstFocusTarget(sheet);
        if (target) target.focus();
    }

    function closeSheet(sheet) {
        var documentType = sheet.dataset.documentType || "";
        if (sheet.open && typeof sheet.close === "function") sheet.close();
        sheet.remove();
        if (current && current.sheet === sheet) current = null;
        focusTrigger(documentType);
    }

    function onSwap() {
        // The swapped-in tile replaces the old target and the undo bar arrives
        // out of band, so look at the whole page.
        scheduleExpiry(document);
        pulseFreshTiles(document);
        var fresh = document.querySelector(SHEET_SELECTOR + ":not([data-tally-sheet-bound])");
        if (fresh) {
            openSheet(fresh);
        } else if (current && !current.sheet.isConnected) {
            // The save emptied the slot out of band: hand the focus back to the tile.
            var documentType = current.documentType;
            current = null;
            focusTrigger(documentType);
        }
    }

    function isOutside(sheet, x, y) {
        var rect = sheet.getBoundingClientRect();
        return x < rect.left || x > rect.right || y < rect.top || y > rect.bottom;
    }

    document.body.addEventListener("htmx:afterSwap", onSwap);
    document.body.addEventListener("htmx:oobAfterSwap", onSwap);
    document.body.addEventListener("keydown", function (event) {
        if (event.key !== "Escape" || !current || !current.sheet.isConnected) return;
        event.preventDefault();
        closeSheet(current.sheet);
    });
    // A modal <dialog> raises "cancel" on Escape; our own close runs instead.
    document.body.addEventListener(
        "cancel",
        function (event) {
            if (event.target && event.target.matches && event.target.matches(SHEET_SELECTOR)) {
                event.preventDefault();
                closeSheet(event.target);
            }
        },
        true
    );
    document.body.addEventListener("click", function (event) {
        var closer = event.target.closest && event.target.closest("[data-tally-number-close]");
        var owner = closer && closer.closest(SHEET_SELECTOR);
        if (owner) {
            event.preventDefault();
            closeSheet(owner);
            return;
        }
        // Backdrop of the modal bottom sheet: the click lands on the dialog itself.
        if (
            current &&
            event.target === current.sheet &&
            current.sheet.getAttribute("aria-modal") === "true" &&
            isOutside(current.sheet, event.clientX, event.clientY)
        ) {
            closeSheet(current.sheet);
        }
    });
    // Popover (non-modal): a press outside closes it.
    document.body.addEventListener("mousedown", function (event) {
        if (!current || !current.sheet.isConnected) return;
        if (current.sheet.getAttribute("aria-modal") === "true") return;
        if (current.sheet.contains(event.target)) return;
        var trigger = triggerFor(current.documentType);
        if (trigger && trigger.contains(event.target)) return;
        closeSheet(current.sheet);
    });

    window.tallyNumberSheet = { open: openSheet, close: closeSheet };

    document.addEventListener("DOMContentLoaded", function () {
        scheduleExpiry(document);
        pulseFreshTiles(document);
    });
})();
