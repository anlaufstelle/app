/*
 * Zeitstrom header selection: shift <select> and mobile date/shift sheet.
 *
 * The shift is a native <select>. In the feed it carries the HTMX attributes
 * (the feed reloads without a page load); in the handover it navigates to the
 * address stored on the chosen <option>. The select keeps the class
 * ``time-filter-tab`` because ``zeitstrom-filter-state.js`` and the live watch
 * identify the transition request by it; ``data-zeitstrom-time-filter`` has to
 * hold the chosen value *before* HTMX serializes the request, so the change
 * listener runs in the capture phase.
 *
 * Inline HTMX handlers are not allowed under the strict CSP (#690), hence this
 * external script.
 *
 * Mobile: the date/shift trigger opens ``#zeitstrom-selection`` as a bottom
 * sheet. Escape, a tap on the backdrop, the close button and choosing a shift
 * close it; focus moves into the sheet on open and back to the trigger on
 * close. From the ``md`` breakpoint the same element is an ordinary inline
 * control row, so there is only one date navigation and one shift select.
 */
(function () {
    "use strict";

    var SELECT_ID = "zeitstrom-shift-select";
    var SHEET_CLASSES = [
        "fixed",
        "inset-x-0",
        "bottom-0",
        "z-50",
        "flex",
        "flex-col",
        "gap-4",
        "p-4",
        "bg-surface",
        "border-t",
        "border-subtle",
        "rounded-t-xl",
        "shadow-lg",
        "max-h-[85vh]",
        "overflow-y-auto",
    ];

    function byId(id) {
        return document.getElementById(id);
    }

    function navigate(url) {
        if (typeof window.__zeitstromNavigate === "function") {
            window.__zeitstromNavigate(url);
            return;
        }
        window.location.assign(url);
    }

    function selectedOption(select) {
        return select.options[select.selectedIndex] || null;
    }

    function syncDateLinks(value) {
        document.querySelectorAll("a[data-date-nav-link]").forEach(function (link) {
            var href = link.getAttribute("href") || "";
            var query = href.indexOf("?") === -1 ? "" : href.slice(href.indexOf("?") + 1);
            var params = new URLSearchParams(query);
            params.set("time_filter", value);
            link.setAttribute("href", "?" + params.toString());
        });
    }

    function syncTrigger(select) {
        var trigger = byId("zeitstrom-selection-trigger");
        var option = selectedOption(select);
        if (!trigger || !option) return;
        var shift = trigger.querySelector("[data-sheet-shift]");
        var range = trigger.querySelector("[data-sheet-range]");
        if (shift) shift.textContent = option.dataset.label || option.textContent.trim();
        if (range) {
            var text = option.dataset.range || "";
            if (text && option.dataset.live === "1" && trigger.dataset.liveLabel) {
                text += " · " + trigger.dataset.liveLabel;
            }
            range.textContent = text;
        }
    }

    function sheet() {
        return byId("zeitstrom-selection");
    }

    function isOpen() {
        var el = sheet();
        var trigger = byId("zeitstrom-selection-trigger");
        return !!el && !!trigger && trigger.getAttribute("aria-expanded") === "true";
    }

    function openSheet() {
        var el = sheet();
        var trigger = byId("zeitstrom-selection-trigger");
        var backdrop = byId("zeitstrom-selection-backdrop");
        if (!el || !trigger) return;
        el.classList.remove("hidden");
        SHEET_CLASSES.forEach(function (cls) {
            el.classList.add(cls);
        });
        el.setAttribute("role", "dialog");
        el.setAttribute("aria-label", trigger.textContent.replace(/\s+/g, " ").trim());
        if (backdrop) backdrop.classList.remove("hidden");
        trigger.setAttribute("aria-expanded", "true");
        var first = el.querySelector("a[href], select, button");
        if (first) first.focus();
    }

    function closeSheet(returnFocus) {
        var el = sheet();
        var trigger = byId("zeitstrom-selection-trigger");
        var backdrop = byId("zeitstrom-selection-backdrop");
        if (!el || !trigger) return;
        SHEET_CLASSES.forEach(function (cls) {
            el.classList.remove(cls);
        });
        el.classList.add("hidden");
        el.removeAttribute("role");
        el.removeAttribute("aria-label");
        if (backdrop) backdrop.classList.add("hidden");
        trigger.setAttribute("aria-expanded", "false");
        if (returnFocus !== false) trigger.focus();
    }

    // Capture phase: runs before the HTMX listener on the select itself.
    document.addEventListener(
        "change",
        function (event) {
            var select = event.target;
            if (!select || select.id !== SELECT_ID) return;
            var value = select.value;
            select.dataset.zeitstromTimeFilter = value;
            syncDateLinks(value);
            syncTrigger(select);
            if (isOpen()) closeSheet();
            if (select.dataset.navigate === "1") {
                var option = selectedOption(select);
                if (option && option.dataset.href) navigate(option.dataset.href);
            }
        },
        true,
    );

    document.addEventListener("click", function (event) {
        var target = event.target;
        if (!target || !target.closest) return;
        if (target.closest("#zeitstrom-selection-trigger")) {
            if (isOpen()) {
                closeSheet();
            } else {
                openSheet();
            }
            return;
        }
        if (!isOpen()) return;
        if (target.closest("[data-sheet-close]") || target.closest("[data-sheet-backdrop]")) {
            closeSheet();
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && isOpen()) {
            event.preventDefault();
            closeSheet();
        }
    });
})();
