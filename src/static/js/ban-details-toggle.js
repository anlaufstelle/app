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
 * The focus is not touched by a swap here: the guard in focus-management.js hands
 * it to the same control (the controls carry stable ids). When the details are
 * hidden while the close button holds the focus, the focus moves to the figure
 * first, so it never falls to <body>.
 *
 * Freshness. The KPI slot and the ban slot each carry what the server said when it
 * rendered them: the fingerprint of the list and whether the details are hidden
 * (data-ban-fingerprint / data-fingerprint, data-ban-details-hidden). This script
 * never rewrites those statements except with an own answer it has proven to be
 * newer than both slots. What is displayed follows from them:
 *  - The details are hidden only while both slots say "hidden" for the same list.
 *    If the slots disagree (another list, or one hidden and one shown) the details
 *    are shown, and when the disagreement outlasts a short grace period the live
 *    watch is asked for both slots again.
 *  - The page counts every replaced slot (``slotGeneration``). The answer to an
 *    own request (hide or show) is used only if no slot was replaced since the
 *    request started; equal fingerprints are no proof, a list can change and come
 *    back. A discarded answer, and any failed request, leaves the details visible
 *    and asks the live watch for both slots again.
 *  - "Asking the live watch" (event ``zeitstrom:ban-slots-stale``) makes it drop
 *    its validators for both slots, abort a cycle in flight and poll at once. The
 *    slots then come through the watch's own snapshot and generation checks, so an
 *    older answer can never overwrite a newer one, and the server answers 200
 *    instead of 304 until the page holds both slots again.
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
    var HIDDEN_ATTR = "data-ban-details-hidden";

    var STALE_EVENT = "zeitstrom:ban-slots-stale";
    var REFRESH_GRACE_MS = 1500;
    var MAX_REFRESH_ROUNDS = 2;

    var pending = false;
    var knownKpiSlot = null;
    var knownBansSlot = null;
    // Rises with every replaced KPI or ban slot.
    var slotGeneration = 0;
    var refreshTimer = null;
    var refreshRounds = 0;

    function byId(id) {
        return document.getElementById(id);
    }

    // The list on screen: the fingerprint of the ban block ("" without bans).
    function listFingerprint() {
        var bansSlot = byId(BANS_SLOT_ID);
        return (bansSlot && bansSlot.getAttribute("data-fingerprint")) || "";
    }

    function saysHidden(slot) {
        return !!slot && slot.getAttribute(HIDDEN_ATTR) === "true";
    }

    // Show or hide the block and bring the figure and the close button in line.
    // Changes only what is displayed, never the slots' own statements.
    function display(hidden) {
        var details = byId(DETAILS_ID);
        var toggle = byId(TOGGLE_ID);
        var close = byId(CLOSE_ID);
        var fingerprint = listFingerprint();

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
    }

    // Record an own answer that is proven newer than both slots.
    function recordAnswer(hidden) {
        [byId(BANS_SLOT_ID), byId(KPI_SLOT_ID)].forEach(function (slot) {
            if (slot && slot.hasAttribute(HIDDEN_ATTR)) {
                slot.setAttribute(HIDDEN_ATTR, hidden ? "true" : "false");
            }
        });
    }

    // Why the two slots cannot both be right: "list" when they were rendered for
    // different lists, "state" when one says hidden and the other shown, "" when
    // they agree (or there is no KPI slot, as in the handover).
    function disagreement() {
        var kpiSlot = byId(KPI_SLOT_ID);
        var bansSlot = byId(BANS_SLOT_ID);
        if (!kpiSlot || !kpiSlot.hasAttribute("data-ban-fingerprint")) {
            return "";
        }
        if ((kpiSlot.getAttribute("data-ban-fingerprint") || "") !== listFingerprint()) {
            return "list";
        }
        if (bansSlot && bansSlot.hasAttribute(HIDDEN_ATTR) && saysHidden(kpiSlot) !== saysHidden(bansSlot)) {
            return "state";
        }
        return "";
    }

    // What the slots say together: hidden only if the ban slot says so and the
    // KPI slot (if there is one with a statement) says so as well.
    function slotsSayHidden() {
        var kpiSlot = byId(KPI_SLOT_ID);
        var kpiSays = !kpiSlot || !kpiSlot.hasAttribute(HIDDEN_ATTR) || saysHidden(kpiSlot);
        return saysHidden(byId(BANS_SLOT_ID)) && kpiSays;
    }

    // Count replaced slots.
    function observeSlots() {
        var kpiSlot = byId(KPI_SLOT_ID);
        var bansSlot = byId(BANS_SLOT_ID);
        if (kpiSlot !== knownKpiSlot || bansSlot !== knownBansSlot) {
            slotGeneration += 1;
        }
        knownKpiSlot = kpiSlot;
        knownBansSlot = bansSlot;
    }

    // Ask the live watch for both slots again (see the header).
    function requestWatchRefresh() {
        window.dispatchEvent(new CustomEvent(STALE_EVENT));
    }

    function clearRefreshTimer() {
        if (refreshTimer !== null) {
            window.clearTimeout(refreshTimer);
            refreshTimer = null;
        }
    }

    function armRefresh() {
        if (refreshTimer !== null || refreshRounds >= MAX_REFRESH_ROUNDS) {
            return;
        }
        refreshTimer = window.setTimeout(function () {
            refreshTimer = null;
            if (disagreement()) {
                refreshRounds += 1;
                requestWatchRefresh();
            }
        }, REFRESH_GRACE_MS);
    }

    // Bring the display in line with what the slots say.
    function settle() {
        if (disagreement()) {
            display(false);
            armRefresh();
            return;
        }
        clearRefreshTimer();
        refreshRounds = 0;
        display(slotsSayHidden());
    }

    // An own answer that may be older than the page, or a failed request: keep the
    // details visible and let the live watch bring both slots.
    function giveUpAnswer() {
        display(false);
        requestWatchRefresh();
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

        var wantsHide = button.value.indexOf(HIDE_PREFIX) === 0;
        var requestedList = wantsHide ? listFingerprint() || button.value.slice(HIDE_PREFIX.length) : "";
        var choice = wantsHide ? HIDE_PREFIX + requestedList : "show";

        observeSlots();
        var startGeneration = slotGeneration;
        pending = true;
        display(wantsHide);
        var answer = null;
        try {
            answer = await send(form, choice);
        } catch (_e) {
            answer = null;
        }
        pending = false;
        observeSlots();
        if (answer === null || slotGeneration !== startGeneration) {
            giveUpAnswer();
            return;
        }
        // No slot was replaced while the request ran, so the answer is newer than
        // both. A hide counts only for the list that was asked for.
        if (wantsHide && listFingerprint() === requestedList && answer.fingerprint === requestedList) {
            recordAnswer(!!answer.hidden);
        } else {
            recordAnswer(false);
        }
        settle();
    }

    // A slot was replaced (live watch, feed refresh): count it and, unless an own
    // request is in flight, display what the slots say together.
    function reconcile() {
        observeSlots();
        if (pending) {
            return;
        }
        settle();
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
