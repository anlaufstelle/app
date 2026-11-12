/*
 * Demo notice: fold the details on small screens (Refs #2663).
 *
 * Without this script the complete notice is visible; the toggle and the
 * static summary are server-rendered so that case needs no fallback code.
 * Once the script runs it swaps the static summary for the toggle button,
 * which carries the same text, and folds the details unless the visitor
 * unfolded them earlier in this browser session. The summary line itself is
 * never hidden: the marking of synthetic data and the reset time are a
 * transparency duty of the public demo.
 *
 * The folded state is a data attribute on the banner; the stylesheet hides
 * the details from it only below the md breakpoint, so wide screens always
 * show everything.
 */
(function () {
    "use strict";

    var STORAGE_KEY = "anlaufstelle.demoBanner.open";

    var banner = document.getElementById("demo-banner");
    if (!banner) return;
    var toggle = banner.querySelector("[data-demo-banner-toggle]");
    if (!toggle) return;
    var summary = banner.querySelector("[data-demo-banner-summary]");

    function readOpen() {
        try {
            return window.sessionStorage.getItem(STORAGE_KEY) === "1";
        } catch (error) {
            return false; // Storage blocked: stay folded, toggling still works in memory.
        }
    }

    function writeOpen(open) {
        try {
            window.sessionStorage.setItem(STORAGE_KEY, open ? "1" : "0");
        } catch (error) {
            // Not remembered across pages; the current page is unaffected.
        }
    }

    function apply(open) {
        if (open) {
            banner.removeAttribute("data-collapsed");
        } else {
            banner.setAttribute("data-collapsed", "");
        }
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }

    if (summary) summary.hidden = true;
    toggle.hidden = false;
    apply(readOpen());

    toggle.addEventListener("click", function () {
        var open = toggle.getAttribute("aria-expanded") !== "true";
        apply(open);
        writeOpen(open);
    });
})();
