// Number entry of the tally: popover on desktop, bottom sheet on small screens.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const TYPE = "11111111-1111-4111-8111-111111111111";
let dialogCalls;
let desktop;

function stubDialog() {
    // jsdom has no modal dialog support; record what the script asks for.
    dialogCalls = [];
    HTMLDialogElement.prototype.show = function () {
        dialogCalls.push("show");
        this.setAttribute("open", "");
    };
    HTMLDialogElement.prototype.showModal = function () {
        dialogCalls.push("showModal");
        this.setAttribute("open", "");
    };
    HTMLDialogElement.prototype.close = function () {
        dialogCalls.push("close");
        this.removeAttribute("open");
    };
}

beforeEach(async () => {
    stubDialog();
    desktop = true;
    window.matchMedia = vi.fn((query) => ({
        matches: query.includes("min-width: 768px") ? desktop : false,
        media: query,
        addEventListener() {},
        removeEventListener() {},
    }));
    // Fresh <body>: listeners of earlier module instances must not leak in.
    document.body = document.createElement("body");
    vi.resetModules();
    await import("../../src/static/js/tally-undo.js");
});

afterEach(() => {
    document.body.innerHTML = "";
});

function panel() {
    document.body.innerHTML = `<section id="tally-panel" class="relative">
        <div class="grid"><div id="tally-row-${TYPE}" data-testid="tally-row-${TYPE}"><div id="tally-action-${TYPE}"><a href="/x" data-testid="tally-increment-${TYPE}" hx-get="/x">+ …</a></div></div></div>
        <div id="tally-number-form-slot" data-hx-no-focus></div>
        <div id="tally-undo-slot"></div></section>`;
    return document.getElementById("tally-number-form-slot");
}

function sheetHtml({ invalid = false } = {}) {
    return `<dialog open id="tally-number-form-${TYPE}" data-tally-number-sheet data-document-type="${TYPE}" data-testid="tally-inline-row-${TYPE}" aria-labelledby="title-${TYPE}">
        <h2 id="title-${TYPE}">Spritzentausch</h2>
        <button type="button" data-tally-number-close aria-label="Schließen">×</button>
        <form data-testid="tally-inline-form-${TYPE}">
            <input name="a" data-testid="first-field">
            <input name="b" ${invalid ? 'aria-invalid="true"' : ""} data-testid="second-field">
            <a href="/" data-tally-number-close data-testid="tally-cancel-${TYPE}">Abbrechen</a>
            <button type="submit">Kontakt zählen</button>
        </form></dialog>`;
}

function swapIn(slot, html) {
    slot.innerHTML = html;
    document.body.dispatchEvent(new CustomEvent("htmx:afterSwap", { bubbles: true }));
}

const trigger = () => document.querySelector(`[data-testid="tally-increment-${TYPE}"]`);
const sheet = () => document.querySelector("[data-tally-number-sheet]");

describe("opening", () => {
    it("opens a non-modal popover on desktop, anchored to the tile, focusing the first field", () => {
        const slot = panel();

        swapIn(slot, sheetHtml());

        expect(dialogCalls).toEqual(["show"]);
        expect(sheet().hasAttribute("aria-modal")).toBe(false);
        expect(document.activeElement.getAttribute("data-testid")).toBe("first-field");
        // Anchoring happens through the CSS object model (CSP-safe), not an attribute in the markup.
        expect(sheet().style.top).not.toBe("");
        expect(sheet().style.left).not.toBe("");
    });

    it("opens a modal bottom sheet on small screens and marks it aria-modal", () => {
        desktop = false;
        const slot = panel();

        swapIn(slot, sheetHtml());

        expect(dialogCalls).toEqual(["showModal"]);
        expect(sheet().getAttribute("aria-modal")).toBe("true");
        expect(sheet().style.top).toBe("");
        expect(document.activeElement.getAttribute("data-testid")).toBe("first-field");
    });

    it("focuses the first invalid field after a validation re-render", () => {
        const slot = panel();

        swapIn(slot, sheetHtml({ invalid: true }));

        expect(document.activeElement.getAttribute("data-testid")).toBe("second-field");
    });

    it("keeps a single open sheet when the server re-renders the form", () => {
        const slot = panel();
        swapIn(slot, sheetHtml());

        swapIn(slot, sheetHtml({ invalid: true }));

        expect(document.querySelectorAll("[data-tally-number-sheet]")).toHaveLength(1);
        expect(sheet().hasAttribute("open")).toBe(true);
    });
});

describe("closing returns focus to the tile", () => {
    function opened() {
        const slot = panel();
        trigger().focus();
        swapIn(slot, sheetHtml());
        return slot;
    }

    it("closes on Escape", () => {
        const slot = opened();

        sheet().dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));

        expect(slot.children).toHaveLength(0);
        expect(document.activeElement).toBe(trigger());
    });

    it("closes on Escape pressed inside a field", () => {
        const slot = opened();

        document.querySelector('[data-testid="first-field"]').dispatchEvent(
            new KeyboardEvent("keydown", { key: "Escape", bubbles: true })
        );

        expect(slot.children).toHaveLength(0);
        expect(document.activeElement).toBe(trigger());
    });

    it("closes through the cancel link without following it", () => {
        const slot = opened();
        const click = new MouseEvent("click", { bubbles: true, cancelable: true });

        document.querySelector(`[data-testid="tally-cancel-${TYPE}"]`).dispatchEvent(click);

        expect(click.defaultPrevented).toBe(true);
        expect(slot.children).toHaveLength(0);
        expect(document.activeElement).toBe(trigger());
    });

    it("closes through the close button", () => {
        const slot = opened();

        document.querySelector("button[data-tally-number-close]").click();

        expect(slot.children).toHaveLength(0);
        expect(document.activeElement).toBe(trigger());
    });

    it("closes on a click outside the popover but not on a click inside", () => {
        const slot = opened();

        document.querySelector('[data-testid="first-field"]').dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
        expect(slot.children).toHaveLength(1);

        document.getElementById("tally-undo-slot").dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
        expect(slot.children).toHaveLength(0);
        expect(document.activeElement).toBe(trigger());
    });

    it("closes the bottom sheet when its backdrop is clicked", () => {
        desktop = false;
        const slot = opened();

        sheet().dispatchEvent(new MouseEvent("click", { bubbles: true, clientX: 500, clientY: 500 }));

        expect(slot.children).toHaveLength(0);
        expect(document.activeElement).toBe(trigger());
    });

    it("returns focus to the refreshed tile after a successful save empties the slot", () => {
        const slot = opened();
        const row = document.getElementById(`tally-row-${TYPE}`);
        // Out-of-band empty slot plus the outerHTML swap of the tile.
        slot.innerHTML = "";
        row.outerHTML = `<div id="tally-row-${TYPE}" data-testid="tally-row-${TYPE}"><a href="/x" data-testid="tally-increment-${TYPE}">+ …</a></div>`;
        document.body.dispatchEvent(new CustomEvent("htmx:oobAfterSwap", { bubbles: true }));

        expect(document.activeElement).toBe(trigger());
    });

    it("does not steal focus when nothing was open", () => {
        panel();
        const other = document.createElement("input");
        document.body.appendChild(other);
        other.focus();

        document.body.dispatchEvent(new CustomEvent("htmx:oobAfterSwap", { bubbles: true }));

        expect(document.activeElement).toBe(other);
    });
});

describe("programmatic API for the offline form", () => {
    it("opens a sheet that was built in the DOM and closes it again", () => {
        const slot = panel();
        slot.innerHTML = sheetHtml();

        window.tallyNumberSheet.open(sheet(), trigger());

        expect(dialogCalls).toEqual(["show"]);
        window.tallyNumberSheet.close(sheet());
        expect(slot.children).toHaveLength(0);
        expect(document.activeElement).toBe(trigger());
    });
});
