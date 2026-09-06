// Refs #1059 (A11Y-017/008): Die Zeitfilter-Tableiste wird beim Umschalten
// NICHT neu gerendert — der HTMX-Swap trifft die Feed-Liste. Alles, was
// time-filter-tabs.js nicht mitzieht, bleibt auf dem Server-Stand stehen.
// Vorher zog das Skript nur die Tailwind-Stilklassen mit: ``aria-selected``
// blieb am alten Tab haengen (der aktive Tab war rein visuell), und die
// Uhrzeit-Spanne behielt ``text-ink-muted``, das auf ``bg-accent-light`` nur
// 4,31:1 erreicht.
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

beforeAll(async () => {
    await import("../../src/static/js/time-filter-tabs.js");
});

function tab(id, { active = false, withTime = true } = {}) {
    const button = document.createElement("button");
    button.id = id;
    button.setAttribute("role", "tab");
    button.className = "time-filter-tab";
    button.setAttribute("aria-selected", active ? "true" : "false");
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

describe("aria-selected wandert mit (A11Y-017)", () => {
    it("setzt den angeklickten Tab auf true und alle anderen auf false", () => {
        const alle = tab("alle", { active: true, withTime: false });
        const nacht = tab("nacht");
        const tagsueber = tab("tagsueber");

        click(nacht);

        expect(nacht.getAttribute("aria-selected")).toBe("true");
        expect(alle.getAttribute("aria-selected")).toBe("false");
        expect(tagsueber.getAttribute("aria-selected")).toBe("false");
    });

    it("laesst genau EINEN Tab ausgewaehlt, auch nach mehrfachem Umschalten", () => {
        const tabs = [tab("a", { active: true }), tab("b"), tab("c")];

        click(tabs[1]);
        click(tabs[2]);
        click(tabs[0]);

        const selected = tabs.filter((t) => t.getAttribute("aria-selected") === "true");
        expect(selected).toEqual([tabs[0]]);
    });

    it("ignoriert Elemente ohne .time-filter-tab", () => {
        const aktiv = tab("aktiv", { active: true });
        const fremd = document.createElement("button");
        document.body.appendChild(fremd);

        click(fremd);

        expect(aktiv.getAttribute("aria-selected")).toBe("true");
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
        expect(alle.getAttribute("aria-selected")).toBe("true");
    });
});
