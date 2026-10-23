// Refs #1059 (A11Y-017/008): HTMX swaps the feed list, leaving time-filter
// controls mounted. The script must update their selection and contrast.
// Previously only Tailwind classes changed: selection stayed on the old
// button and the time range retained text-ink-muted, whose contrast on
// bg-accent-light is only 4.31:1.
//
// Der Zustand steht in ``aria-pressed`` und NICHT in ``aria-selected``: die
// Knoepfe sind unabhaengige Umschalter, kein ARIA-Tab-Widget (kein
// Roving-Tabindex, keine Pfeilnavigation, kein zugeordnetes ``tabpanel``).

import { beforeAll, beforeEach, describe, expect, it } from "vitest";

beforeAll(async () => {
    await import("../../src/static/js/time-filter-tabs.js");
});

function tab(id, { active = false, withTime = true } = {}) {
    const button = document.createElement("button");
    button.id = id;
    button.className = "time-filter-tab";
    button.setAttribute("aria-pressed", active ? "true" : "false");
    if (withTime) {
        const span = document.createElement("span");
        span.className = `time-filter-tab-time ${active ? "text-accent" : "text-ink-muted"}`;
        button.appendChild(span);
    }
    document.body.appendChild(button);
    return button;
}

/** Feuert ``htmx:beforeRequest`` so, wie htmx es tut: am Quell-Element. */
function click(element) {
    element.dispatchEvent(new CustomEvent("htmx:beforeRequest", { bubbles: true }));
}

beforeEach(() => {
    document.body.innerHTML = "";
});

describe("aria-pressed wandert mit (A11Y-017)", () => {
    it("setzt den angeklickten Tab auf true und alle anderen auf false", () => {
        const alle = tab("alle", { active: true, withTime: false });
        const nacht = tab("nacht");
        const tagsueber = tab("tagsueber");

        click(nacht);

        expect(nacht.getAttribute("aria-pressed")).toBe("true");
        expect(alle.getAttribute("aria-pressed")).toBe("false");
        expect(tagsueber.getAttribute("aria-pressed")).toBe("false");
    });

    it("laesst genau EINEN Knopf gedrueckt, auch nach mehrfachem Umschalten", () => {
        const tabs = [tab("a", { active: true }), tab("b"), tab("c")];

        click(tabs[1]);
        click(tabs[2]);
        click(tabs[0]);

        const pressed = tabs.filter((t) => t.getAttribute("aria-pressed") === "true");
        expect(pressed).toEqual([tabs[0]]);
    });

    it("ignoriert Elemente ohne .time-filter-tab", () => {
        const aktiv = tab("aktiv", { active: true });
        const fremd = document.createElement("button");
        document.body.appendChild(fremd);

        click(fremd);

        expect(aktiv.getAttribute("aria-pressed")).toBe("true");
    });
});

describe("Kontrast der Uhrzeit-Spanne (A11Y-008)", () => {
    function time(button) {
        return button.querySelector(".time-filter-tab-time").className;
    }

    it("faerbt die Spanne des aktiven Tabs auf text-accent um", () => {
        const alt = tab("alt", { active: true });
        const neu = tab("neu");

        click(neu);

        expect(time(neu)).toContain("text-accent");
        expect(time(neu)).not.toContain("text-ink-muted");
    });

    it("nimmt sie dem vorher aktiven Tab wieder weg", () => {
        const alt = tab("alt", { active: true });
        const neu = tab("neu");

        click(neu);

        expect(time(alt)).toContain("text-ink-muted");
        expect(time(alt)).not.toContain("text-accent");
    });

    it("kommt mit dem Alle-Tab ohne Uhrzeit-Spanne zurecht", () => {
        const alle = tab("alle", { withTime: false });
        tab("nacht", { active: true });

        expect(() => click(alle)).not.toThrow();
        expect(alle.getAttribute("aria-pressed")).toBe("true");
    });
});
