// Strichlisten-Skript: Farbpuls der Kachel nach einem frischen Strich.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const PULSE_MS = 450;
const PULSE_CLASSES = ["bg-accent-light", "border-accent"];

beforeEach(async () => {
    vi.useFakeTimers();
    document.body.innerHTML = "";
    vi.resetModules();
    await import("../../src/static/js/tally-undo.js");
});

afterEach(() => {
    vi.useRealTimers();
});

function tile(attrs = "data-tally-pulse") {
    document.body.innerHTML = `<section id="tally-panel"><div id="tally-row-A" data-testid="tally-row-A" class="border border-subtle bg-surface" ${attrs}></div></section>`;
    return document.getElementById("tally-row-A");
}

function swapped() {
    document.body.dispatchEvent(new CustomEvent("htmx:afterSwap", { bubbles: true }));
}

describe("tile colour pulse", () => {
    it("highlights a freshly flagged tile and removes the highlight after ~450 ms", () => {
        const row = tile();

        swapped();

        PULSE_CLASSES.forEach((cls) => expect(row.classList.contains(cls)).toBe(true));
        vi.advanceTimersByTime(PULSE_MS - 1);
        PULSE_CLASSES.forEach((cls) => expect(row.classList.contains(cls)).toBe(true));
        vi.advanceTimersByTime(2);
        PULSE_CLASSES.forEach((cls) => expect(row.classList.contains(cls)).toBe(false));
        expect(row.classList.contains("bg-surface")).toBe(true);
    });

    it("consumes the flag so a later swap does not pulse the same tile again", () => {
        const row = tile();

        swapped();
        vi.advanceTimersByTime(PULSE_MS + 10);
        swapped();

        expect(row.hasAttribute("data-tally-pulse")).toBe(false);
        PULSE_CLASSES.forEach((cls) => expect(row.classList.contains(cls)).toBe(false));
    });

    it("leaves tiles without the flag untouched", () => {
        const row = tile("");

        swapped();

        PULSE_CLASSES.forEach((cls) => expect(row.classList.contains(cls)).toBe(false));
    });

    it("pulses a flagged tile that is present when the page is ready", () => {
        const row = tile();

        document.dispatchEvent(new Event("DOMContentLoaded"));

        PULSE_CLASSES.forEach((cls) => expect(row.classList.contains(cls)).toBe(true));
    });
});
