// Header selection (date + shift): the native shift <select> keeps the filter
// state in sync, the navigation links follow the shift, and on small screens
// the date/shift trigger opens a sheet that closes on Escape or outside tap and
// returns focus to the trigger.

import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

beforeAll(async () => {
    await import("../../src/static/js/time-filter-tabs.js");
});

const PAGE = `
<button id="zeitstrom-selection-trigger" aria-expanded="false" aria-controls="zeitstrom-selection" data-live-label="läuft">
  <span data-sheet-date>Fr, 23. Oktober</span>
  <span data-sheet-shift>Ganzer Tag</span>
  <span data-sheet-range></span>
</button>
<div id="zeitstrom-selection-backdrop" class="hidden" data-sheet-backdrop></div>
<div id="zeitstrom-selection" class="hidden md:flex" data-sheet>
  <button type="button" data-sheet-close>Schließen</button>
  <a id="prev" data-date-nav-link href="?date=2026-10-22&time_filter=old">prev</a>
  <a id="today" href="/zeitstrom/">today</a>
  <select id="zeitstrom-shift-select" class="time-filter-tab" data-zeitstrom-time-filter="all">
    <option value="all" data-label="Ganzer Tag" data-range="" data-href="?date=2026-10-23&time_filter=all" selected>Ganzer Tag</option>
    <option value="night" data-label="Nachtdienst" data-range="22:00–06:00 (+1)" data-live="1" data-href="?date=2026-10-23&time_filter=night">Nachtdienst</option>
  </select>
</div>`;

function choose(value, { navigate = false } = {}) {
    const select = document.getElementById("zeitstrom-shift-select");
    if (navigate) select.dataset.navigate = "1";
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    return select;
}

beforeEach(() => {
    document.body.innerHTML = PAGE;
    window.__navigated = null;
    window.__zeitstromNavigate = (url) => {
        window.__navigated = url;
    };
});

describe("shift select keeps the filter state in sync", () => {
    it("writes the chosen shift into the data attribute read by the request config", () => {
        const select = choose("night");
        expect(select.dataset.zeitstromTimeFilter).toBe("night");
    });

    it("moves the shift parameter of the date links along", () => {
        choose("night");
        expect(document.getElementById("prev").getAttribute("href")).toContain("time_filter=night");
        expect(document.getElementById("prev").getAttribute("href")).toContain("date=2026-10-22");
        choose("all");
        expect(document.getElementById("prev").getAttribute("href")).toContain("time_filter=all");
    });

    it("leaves links without the date-link marker alone", () => {
        choose("night");
        expect(document.getElementById("today").getAttribute("href")).toBe("/zeitstrom/");
    });

    it("updates the mobile trigger text with shift, window and running marker", () => {
        choose("night");
        expect(document.querySelector("[data-sheet-shift]").textContent).toBe("Nachtdienst");
        expect(document.querySelector("[data-sheet-range]").textContent).toBe("22:00–06:00 (+1) · läuft");
        choose("all");
        expect(document.querySelector("[data-sheet-shift]").textContent).toBe("Ganzer Tag");
        expect(document.querySelector("[data-sheet-range]").textContent).toBe("");
    });
});

describe("handover mode navigates instead of swapping", () => {
    it("opens the option's address on change", () => {
        choose("night", { navigate: true });
        expect(window.__navigated).toBe("?date=2026-10-23&time_filter=night");
    });

    it("does not navigate in feed mode", () => {
        choose("night");
        expect(window.__navigated).toBeNull();
    });
});

describe("mobile sheet", () => {
    const trigger = () => document.getElementById("zeitstrom-selection-trigger");
    const sheet = () => document.getElementById("zeitstrom-selection");
    const backdrop = () => document.getElementById("zeitstrom-selection-backdrop");

    it("opens on tap and exposes the state", () => {
        trigger().click();
        expect(trigger().getAttribute("aria-expanded")).toBe("true");
        expect(sheet().classList.contains("hidden")).toBe(false);
        expect(backdrop().classList.contains("hidden")).toBe(false);
        expect(sheet().getAttribute("role")).toBe("dialog");
    });

    it("moves focus into the sheet", () => {
        trigger().focus();
        trigger().click();
        expect(sheet().contains(document.activeElement)).toBe(true);
    });

    it("closes on Escape and returns focus to the trigger", () => {
        trigger().click();
        document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
        expect(trigger().getAttribute("aria-expanded")).toBe("false");
        expect(sheet().classList.contains("hidden")).toBe(true);
        expect(document.activeElement).toBe(trigger());
    });

    it("closes on a tap outside the sheet", () => {
        trigger().click();
        backdrop().click();
        expect(trigger().getAttribute("aria-expanded")).toBe("false");
        expect(backdrop().classList.contains("hidden")).toBe(true);
        expect(document.activeElement).toBe(trigger());
    });

    it("closes with the close button", () => {
        trigger().click();
        document.querySelector("[data-sheet-close]").click();
        expect(trigger().getAttribute("aria-expanded")).toBe("false");
    });

    it("closes after a shift was chosen", () => {
        trigger().click();
        choose("night");
        expect(trigger().getAttribute("aria-expanded")).toBe("false");
    });

    it("ignores Escape while closed", () => {
        const spy = vi.fn();
        trigger().addEventListener("focus", spy);
        document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
        expect(spy).not.toHaveBeenCalled();
    });
});
