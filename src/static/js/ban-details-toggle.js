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
 *
 * Every state belongs to one list, named by its fingerprint (the KPI slot and the
 * ban slot both carry it). A hide answer is applied only to the list it was asked
 * for, and a slot that arrives late with another list than the one on screen never
 * hides anything: both slots are shown (the safe side) and, if they still disagree
 * after a short while, both are fetched again from the server.
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

    var RELOAD_GRACE_MS = 1500;
    var MAX_RELOAD_ROUNDS = 2;

    var pending = false;
    var knownKpiSlot = null;
    var knownBansSlot = null;
    var reloadTimer = null;
    var reloadRounds = 0;

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

    // The list on screen: the fingerprint of the ban block ("" without bans).
    function listFingerprint() {
        var bansSlot = byId(BANS_SLOT_ID);
        return (bansSlot && bansSlot.getAttribute("data-fingerprint")) || "";
    }

    // True when the KPI slot was rendered for another list than the ban block.
    function slotsDisagree() {
        var kpiSlot = byId(KPI_SLOT_ID);
        if (!kpiSlot || !kpiSlot.hasAttribute("data-ban-fingerprint")) {
            return false;
        }
        return (kpiSlot.getAttribute("data-ban-fingerprint") || "") !== listFingerprint();
    }

    function swapSlot(id, html) {
        var template = document.createElement("template");
        template.innerHTML = html.trim();
        var fresh = template.content.querySelector("#" + id);
        var current = byId(id);
        if (!fresh || !current) {
            return;
        }
        var guard = window.backgroundSwapGuard;
        var snapshot = guard ? guard.capture(current) : null;
        if (window.htmx && typeof window.htmx.swap === "function") {
            window.htmx.swap(current, fresh.outerHTML, { swapStyle: "outerHTML", settleDelay: 0 });
        } else {
            current.replaceWith(fresh);
        }
        if (snapshot) {
            guard.restore(current, snapshot);
        }
    }

    // Both slots again from the server, for the filter state of the page.
    async function reloadSlots() {
        var root = byId("zeitstrom-live-watch");
        if (!root || !root.dataset.kpiUrl || !root.dataset.bansUrl) {
            return;
        }
        var state = byId("zeitstrom-filter-state");
        var query = state ? new URLSearchParams(new FormData(state)).toString() : "";
        function load(url) {
            return fetch(url + (query ? "?" + query : ""), {
                credentials: "same-origin",
                headers: { Accept: "text/html" },
            }).then(function (response) {
                return response.ok && response.status === 200 ? response.text() : null;
            });
        }
        var pages;
        try {
            pages = await Promise.all([load(root.dataset.kpiUrl), load(root.dataset.bansUrl)]);
        } catch (_e) {
            return;
        }
        if (pages[0] !== null) {
            swapSlot(KPI_SLOT_ID, pages[0]);
        }
        if (pages[1] !== null) {
            swapSlot(BANS_SLOT_ID, pages[1]);
        }
    }

    function armReload() {
        if (reloadTimer !== null || reloadRounds >= MAX_RELOAD_ROUNDS) {
            return;
        }
        reloadTimer = window.setTimeout(function () {
            reloadTimer = null;
            if (slotsDisagree()) {
                reloadRounds += 1;
                reloadSlots();
            }
        }, RELOAD_GRACE_MS);
    }

    // The two slots describe different lists: show the details (never hide on the
    // word of a slot that may be older) and let the server settle it.
    function resolveDisagreement() {
        apply(false);
        armReload();
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
        var wantsHide = button.value.indexOf(HIDE_PREFIX) === 0;
        var requestedList = wantsHide ? listFingerprint() || button.value.slice(HIDE_PREFIX.length) : "";
        var choice = wantsHide ? HIDE_PREFIX + requestedList : "show";

        pending = true;
        apply(wantsHide);
        try {
            var answer = await send(form, choice);
            if (!wantsHide) {
                apply(!!answer.hidden);
            } else if (listFingerprint() === requestedList) {
                // Only the list that was asked for may be hidden.
                apply(!!answer.hidden && answer.fingerprint === requestedList);
            }
        } catch (_e) {
            if (!wantsHide || listFingerprint() === requestedList) {
                apply(wasHidden);
            }
        } finally {
            pending = false;
            if (slotsDisagree()) {
                resolveDisagreement();
            }
        }
    }

    // After the live watch (or a feed refresh) replaced one of the two slots, the
    // other follows the replaced one as long as both describe the same list.
    // Replaced slots are told apart by identity.
    function reconcile() {
        var kpiSlot = byId(KPI_SLOT_ID);
        var bansSlot = byId(BANS_SLOT_ID);
        var kpiFresh = kpiSlot !== knownKpiSlot;
        var bansFresh = bansSlot !== knownBansSlot;
        knownKpiSlot = kpiSlot;
        knownBansSlot = bansSlot;
        if (slotsDisagree()) {
            resolveDisagreement();
            return;
        }
        if (reloadTimer !== null) {
            window.clearTimeout(reloadTimer);
            reloadTimer = null;
        }
        reloadRounds = 0;
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
