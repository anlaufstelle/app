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

describe("undo bar countdown", () => {
    function bar(secondsLeft, { expiresAt } = {}) {
        const at = expiresAt ?? new Date(Date.now() + secondsLeft * 1000).toISOString();
        document.body.innerHTML = `<div id="tally-undo-slot"><div data-tally-undo-bar><form data-tally-undo-expires-at="${at}"><button type="submit" data-testid="tally-undo">Rückgängig<span aria-hidden="true"> · <span data-tally-undo-remaining>${secondsLeft}</span> s</span></button></form></div></div>`;
        document.body.dispatchEvent(new CustomEvent("htmx:afterSwap", { bubbles: true }));
        return document.getElementById("tally-undo-slot");
    }
    const remaining = () => {
        const el = document.querySelector("[data-tally-undo-remaining]");
        return el ? el.textContent : null;
    };

    it("counts the remaining seconds down once per second", () => {
        bar(58);

        expect(remaining()).toBe("58");
        vi.advanceTimersByTime(1000);
        expect(remaining()).toBe("57");
        vi.advanceTimersByTime(3000);
        expect(remaining()).toBe("54");
    });

    it("removes the whole bar when the time is up, leaving the slot in place", () => {
        const slot = bar(2);

        vi.advanceTimersByTime(2100);

        expect(document.querySelector("[data-testid='tally-undo']")).toBeNull();
        expect(slot.querySelector("[data-tally-undo-bar]")).toBeNull();
        expect(document.getElementById("tally-undo-slot")).toBe(slot);
    });

    it("removes an already expired bar immediately", () => {
        bar(0, { expiresAt: new Date(Date.now() - 5000).toISOString() });

        expect(document.querySelector("[data-testid='tally-undo']")).toBeNull();
    });

    it("does not start a second timer when the same bar is scanned again", () => {
        bar(30);
        document.body.dispatchEvent(new CustomEvent("htmx:afterSwap", { bubbles: true }));
        document.body.dispatchEvent(new CustomEvent("htmx:oobAfterSwap", { bubbles: true }));

        vi.advanceTimersByTime(1000);

        expect(remaining()).toBe("29");
        expect(vi.getTimerCount()).toBe(1);
    });

    it("stops ticking once a newer bar replaced the old one", () => {
        const slot = bar(40);
        slot.innerHTML = "";
        vi.advanceTimersByTime(1500);

        expect(vi.getTimerCount()).toBe(0);
    });

    it("keeps the screen reader text static: only the aria-hidden part changes", () => {
        bar(10);
        const button = document.querySelector("[data-testid='tally-undo']");
        const label = button.firstChild.textContent;

        vi.advanceTimersByTime(3000);

        expect(button.firstChild.textContent).toBe(label);
        expect(button.querySelector("[aria-hidden='true']")).not.toBeNull();
        expect(button.querySelector("[data-tally-undo-remaining]").closest("[aria-hidden='true']")).not.toBeNull();
    });
});
