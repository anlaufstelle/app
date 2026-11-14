/*
 * Hide / show the ban details of the Zeitstrom.
 *
 * The ban figure in the KPI row (#zeitstrom-ban-details-toggle) and the close
 * button of the detail block (#zeitstrom-ban-details-close) are submit buttons
 * of the form #zeitstrom-ban-details-form. Without this script they post that
 * form and the server redirects back, so the feature works without script. With
 * the script the click is answered at once and sent in the background; the
 * server keeps the choice per user and facility and answers with the state that
 * applies.
 *
 * The live watch replaces the KPI slot and the ban slot one by one. Both are
 * rendered by the server from the same stored choice, so after a swap of only one
 * of them the other follows it. The focus is not touched by a swap here: the
 * guard in focus-management.js hands it to the same control (the controls carry
 * stable ids). When the details are hidden while the close button holds the focus,
 * the focus moves to the figure first, so it never falls to <body>.
 */
(function () {
    "use strict";

    var FORM_ID = "zeitstrom-ban-details-form";
    var KPI_SLOT_ID = "zeitstrom-kpi-slot";
    var BANS_SLOT_ID = "zeitstrom-bans-slot";
    var DETAILS_ID = "zeitstrom-ban-details";
    var TOGGLE_ID = "zeitstrom-ban-details-toggle";
    var CLOSE_ID = "zeitstrom-ban-details-close";
    var HIDE_PREFIX = "hide:";

    var pending = false;
    var knownKpiSlot = null;
    var knownBansSlot = null;

    function byId(id) {
        return document.getElementById(id);
    }

    function isHidden() {
        var details = byId(DETAILS_ID);
        return !!details && details.hasAttribute("hidden");
    }

    // Show or hide the block and bring the figure, the close button and both slot
    // markers in line with it. Idempotent.
    function apply(hidden) {
        var details = byId(DETAILS_ID);
        var toggle = byId(TOGGLE_ID);
        var close = byId(CLOSE_ID);
        var bansSlot = byId(BANS_SLOT_ID);
        var kpiSlot = byId(KPI_SLOT_ID);
        var fingerprint = bansSlot ? bansSlot.getAttribute("data-fingerprint") : "";

        if (hidden && details && toggle && details.contains(document.activeElement)) {
            toggle.focus({ preventScroll: true });
        }
        if (details) {
            details.hidden = hidden;
        }
        if (toggle) {
            toggle.setAttribute("aria-expanded", hidden ? "false" : "true");
            if (fingerprint || hidden) {
                toggle.value = hidden ? "show" : HIDE_PREFIX + fingerprint;
            }
        }
        if (close && fingerprint) {
            close.value = HIDE_PREFIX + fingerprint;
        }
        [bansSlot, kpiSlot].forEach(function (slot) {
            if (slot && slot.hasAttribute("data-ban-details-hidden")) {
                slot.setAttribute("data-ban-details-hidden", hidden ? "true" : "false");
            }
        });
    }

    function requestBody(form, choice) {
        var body = new FormData(form);
        body.set("ban_details", choice);
        return body;
    }

    async function send(form, choice) {
        var response = await fetch(form.action, {
            method: "POST",
            body: requestBody(form, choice),
            headers: { Accept: "application/json" },
            credentials: "same-origin",
        });
        if (!response.ok) {
            throw new Error("ban details request failed");
        }
        return response.json();
    }

    async function onClick(event) {
        var button = event.target && event.target.closest ? event.target.closest('button[form="' + FORM_ID + '"]') : null;
        var form = byId(FORM_ID);
        if (!button || !form) {
            return;
        }
        event.preventDefault();
        if (pending) {
            return;
        }

        var wasHidden = isHidden();
        var bansSlot = byId(BANS_SLOT_ID);
        var wantsHide = button.value.indexOf(HIDE_PREFIX) === 0;
        var choice = wantsHide ? HIDE_PREFIX + ((bansSlot && bansSlot.getAttribute("data-fingerprint")) || button.value.slice(HIDE_PREFIX.length)) : "show";

        pending = true;
        apply(wantsHide);
        try {
            var answer = await send(form, choice);
            apply(!!answer.hidden);
        } catch (_e) {
            apply(wasHidden);
        } finally {
            pending = false;
        }
    }

    // After the live watch (or a feed refresh) replaced one of the two slots, the
    // other follows the replaced one. Replaced slots are told apart by identity.
    function reconcile() {
        var kpiSlot = byId(KPI_SLOT_ID);
        var bansSlot = byId(BANS_SLOT_ID);
        var kpiFresh = kpiSlot !== knownKpiSlot;
        var bansFresh = bansSlot !== knownBansSlot;
        knownKpiSlot = kpiSlot;
        knownBansSlot = bansSlot;
        if (pending || kpiFresh === bansFresh) {
            return;
        }
        var source = kpiFresh ? kpiSlot : bansSlot;
        if (!source || !source.hasAttribute("data-ban-details-hidden")) {
            return;
        }
        apply(source.getAttribute("data-ban-details-hidden") === "true");
    }

    function start() {
        knownKpiSlot = byId(KPI_SLOT_ID);
        knownBansSlot = byId(BANS_SLOT_ID);
        document.addEventListener("click", onClick);
        var root = byId("zeitstrom-live-watch") || document.body;
        if (window.MutationObserver) {
            new MutationObserver(reconcile).observe(root, { childList: true, subtree: true });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
