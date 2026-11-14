/*
 * Hide / show the ban details of the Zeitstrom.
 *
 * The ban figure in the KPI row (#zeitstrom-ban-details-toggle) and the close
 * button of the detail block (#zeitstrom-ban-details-close) are submit buttons
 * of the form #zeitstrom-ban-details-form. Without this script they post that
 * form and the server redirects back, so the feature works without script. With
 * the script the click is sent in the background; the server keeps the choice
 * per user and facility and answers with the state that applies.
 *
 * Fail-closed rule. The details are visible unless ALL of these hold at once
 * (``shouldHideDetails``, re-evaluated on every event):
 *  - no own hide or show request is open;
 *  - both slots carry the same, non-empty list;
 *  - the ban figure is actually on the page;
 *  - the last confirmed choice is "hidden" and was made for exactly this list.
 * A confirmed "hidden" comes only from the first page render or from an own hide
 * answer that started after the previous confirmed choice and saw no replaced slot
 * while it ran (``choiceGeneration``, ``slotGeneration``). Every confirmed show,
 * and every newly inserted slot that shows another list or says "shown", ends a
 * confirmed "hidden" and raises ``choiceGeneration``. A slot that arrives later
 * therefore never hides anything, whatever it says and however old it is.
 *
 * Server side. An own hide that the page does not accept (a late or failed
 * answer) is compensated by a "show" sent after it, so the stored choice cannot
 * come back after a reload.
 *
 * Repair. When the KPI slot and the ban slot show different lists for longer than
 * a short grace period, the script asks the live watch for both slots again
 * (event ``zeitstrom:ban-slots-stale``); the watch queues that behind a running
 * cycle or filter transition and then fetches both through its own snapshot and
 * generation checks. The repair only fixes the slots; visibility never waits for
 * it.
 *
 * Focus. Swaps are handled by the guard in focus-management.js. When the guard
 * could not hand the focus to the replaced figure or close button (it was hidden in
 * the new slot) and parked it on the slot, that control gets it back after the
 * final evaluation; when the details are hidden while the focus is inside them,
 * the focus moves to the figure first.
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

    // The only mutable decision input besides the slots on the page.
    var state = {
        pending: false,
        confirmedHidden: false,
        confirmedList: "",
        choiceGeneration: 0,
        slotGeneration: 0,
    };
    var knownKpiSlot = null;
    var knownBansSlot = null;
    // A new #zeitstrom-live-watch root is a new first render (a new page).
    var knownRoot = null;
    var pageGeneration = 0;
    var refreshTimer = null;
    var refreshRounds = 0;
    // Id of the figure or close button while it holds the focus.
    var focusedControlId = "";

    function byId(id) {
        return document.getElementById(id);
    }

    // What the page shows right now, read from the slots.
    function readSlots() {
        var kpiSlot = byId(KPI_SLOT_ID);
        var bansSlot = byId(BANS_SLOT_ID);
        var toggle = byId(TOGGLE_ID);
        return {
            kpiList: kpiSlot && kpiSlot.hasAttribute("data-ban-fingerprint") ? kpiSlot.getAttribute("data-ban-fingerprint") || "" : null,
            bansList: (bansSlot && bansSlot.getAttribute("data-fingerprint")) || "",
            figurePresent: !!toggle && !!kpiSlot && kpiSlot.contains(toggle),
        };
    }

    /**
     * The single decision: hide the details only if every condition holds.
     * Pure; the inputs are the slots on the page and the confirmed choice.
     */
    function shouldHideDetails(input) {
        return (
            !input.pending &&
            input.bansList !== "" &&
            input.kpiList === input.bansList &&
            input.figurePresent &&
            input.confirmedHidden &&
            input.confirmedList === input.bansList
        );
    }

    function decisionInput() {
        var slots = readSlots();
        return {
            pending: state.pending,
            kpiList: slots.kpiList,
            bansList: slots.bansList,
            figurePresent: slots.figurePresent,
            confirmedHidden: state.confirmedHidden,
            confirmedList: state.confirmedList,
        };
    }

    function display(hidden) {
        var details = byId(DETAILS_ID);
        var toggle = byId(TOGGLE_ID);
        var close = byId(CLOSE_ID);
        var fingerprint = readSlots().bansList;

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

    // A control that held the focus before its slot was replaced gets it back once
    // it is visible again. Only a focus the swap guard parked on the slot itself is
    // taken back; a focus that the person moved away (also to <body>) stays put.
    function restoreFocus() {
        if (!focusedControlId) {
            return;
        }
        var active = document.activeElement;
        var parked = !!active && (active.id === KPI_SLOT_ID || active.id === BANS_SLOT_ID);
        var control = byId(focusedControlId);
        if (!parked || !control || control === active) {
            return;
        }
        var details = byId(DETAILS_ID);
        if (focusedControlId === CLOSE_ID && details && details.hidden) {
            return;
        }
        control.focus({ preventScroll: true });
    }

    function evaluate() {
        display(shouldHideDetails(decisionInput()));
        restoreFocus();
    }

    function endConfirmedHidden() {
        if (state.confirmedHidden) {
            state.confirmedHidden = false;
            state.choiceGeneration += 1;
        }
    }

    // A newly inserted slot can end a confirmed "hidden" (another list, or it says
    // "shown"), never start one.
    function takeSlotStatement(slot, listAttr) {
        if (!slot || !slot.hasAttribute(listAttr)) {
            return;
        }
        var list = slot.getAttribute(listAttr) || "";
        var saysHidden = slot.getAttribute(HIDDEN_ATTR) === "true";
        if (list !== state.confirmedList || !saysHidden) {
            endConfirmedHidden();
        }
    }

    // Count replaced slots and take what new ones say. A new page root starts over.
    function observeSlots() {
        if (byId("zeitstrom-live-watch") !== knownRoot) {
            initFromPage();
            return;
        }
        var kpiSlot = byId(KPI_SLOT_ID);
        var bansSlot = byId(BANS_SLOT_ID);
        var kpiFresh = kpiSlot !== knownKpiSlot;
        var bansFresh = bansSlot !== knownBansSlot;
        knownKpiSlot = kpiSlot;
        knownBansSlot = bansSlot;
        if (kpiFresh || bansFresh) {
            state.slotGeneration += 1;
        }
        if (kpiFresh) {
            takeSlotStatement(kpiSlot, "data-ban-fingerprint");
        }
        if (bansFresh) {
            takeSlotStatement(bansSlot, "data-fingerprint");
        }
    }

    function listsDisagree() {
        var slots = readSlots();
        return slots.kpiList !== null && slots.kpiList !== slots.bansList;
    }

    function requestWatchRefresh() {
        window.dispatchEvent(new CustomEvent(STALE_EVENT));
    }

    function clearRefreshTimer() {
        if (refreshTimer !== null) {
            window.clearTimeout(refreshTimer);
            refreshTimer = null;
        }
    }

    function scheduleRepair() {
        if (!listsDisagree()) {
            clearRefreshTimer();
            refreshRounds = 0;
            return;
        }
        if (refreshTimer !== null || refreshRounds >= MAX_REFRESH_ROUNDS) {
            return;
        }
        refreshTimer = window.setTimeout(function () {
            refreshTimer = null;
            if (listsDisagree()) {
                refreshRounds += 1;
                requestWatchRefresh();
            }
        }, REFRESH_GRACE_MS);
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

    // The page did not accept an own hide: make sure the server forgets it too.
    // Sent after the hide's answer (or failure), so the server sees it later.
    function compensateHide(form) {
        send(form, "show").catch(function () {
            // Offline: the details stay visible here; the stored choice may apply
            // again after a reload (see the guide).
        });
    }

    async function onClick(event) {
        var button = event.target && event.target.closest ? event.target.closest('button[form="' + FORM_ID + '"]') : null;
        var form = byId(FORM_ID);
        if (!button || !form) {
            return;
        }
        event.preventDefault();
        if (state.pending) {
            return;
        }

        var wantsHide = button.value.indexOf(HIDE_PREFIX) === 0;
        var requestedList = wantsHide ? readSlots().bansList || button.value.slice(HIDE_PREFIX.length) : "";
        observeSlots();
        var startSlots = state.slotGeneration;
        var startChoice = state.choiceGeneration;
        var startPage = pageGeneration;

        state.pending = true;
        evaluate();
        var answer = null;
        try {
            answer = await send(form, wantsHide ? HIDE_PREFIX + requestedList : "show");
        } catch (_e) {
            answer = null;
        }
        if (startPage !== pageGeneration) {
            return;
        }
        state.pending = false;
        observeSlots();

        var fresh = state.slotGeneration === startSlots && state.choiceGeneration === startChoice;
        if (wantsHide) {
            var accepted =
                answer !== null &&
                fresh &&
                !!answer.hidden &&
                answer.fingerprint === requestedList &&
                readSlots().bansList === requestedList;
            if (accepted) {
                state.confirmedHidden = true;
                state.confirmedList = requestedList;
                state.choiceGeneration += 1;
            } else if (answer === null || answer.hidden) {
                compensateHide(form);
            }
        } else if (answer !== null) {
            endConfirmedHidden();
            state.choiceGeneration += 1;
        } else {
            // A failed show: the details stay visible, nothing is confirmed.
            endConfirmedHidden();
        }
        evaluate();
        scheduleRepair();
    }

    // Read the first render of a page: it is the first confirmed choice.
    function initFromPage() {
        var kpiSlot = byId(KPI_SLOT_ID);
        var bansSlot = byId(BANS_SLOT_ID);
        var slots = readSlots();
        knownRoot = byId("zeitstrom-live-watch");
        knownKpiSlot = kpiSlot;
        knownBansSlot = bansSlot;
        pageGeneration += 1;
        state.pending = false;
        clearRefreshTimer();
        refreshRounds = 0;
        state.confirmedHidden =
            slots.bansList !== "" &&
            slots.kpiList === slots.bansList &&
            slots.figurePresent &&
            !!kpiSlot &&
            kpiSlot.getAttribute(HIDDEN_ATTR) === "true" &&
            !!bansSlot &&
            bansSlot.getAttribute(HIDDEN_ATTR) === "true";
        state.confirmedList = state.confirmedHidden ? slots.bansList : "";
        state.choiceGeneration += 1;
    }

    function reconcile() {
        observeSlots();
        evaluate();
        scheduleRepair();
    }

    function onFocusIn(event) {
        var target = event.target;
        if (!target || target === document.body || target.id === KPI_SLOT_ID || target.id === BANS_SLOT_ID) {
            return;
        }
        focusedControlId = target.id === TOGGLE_ID || target.id === CLOSE_ID ? target.id : "";
    }

    function start() {
        initFromPage();
        document.addEventListener("click", onClick);
        document.addEventListener("focusin", onFocusIn);
        if (window.MutationObserver) {
            new MutationObserver(reconcile).observe(knownRoot || document.body, { childList: true, subtree: true });
        }
        evaluate();
    }

    // Test hook: the decision function itself, without page state.
    window.__banDetailsShouldHide = shouldHideDetails;

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
