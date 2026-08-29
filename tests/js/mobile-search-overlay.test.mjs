// Mobiles Such-Overlay der Bottom-Nav (Refs #1861, Loesung A).
//
// Zwei Fehler, eine Flaeche — das Overlay war real nicht bedienbar:
//
//   1. ``toggleSearch`` liess ``moreOpen`` auf ``true``. Das Mehr-Dropdown blieb
//      sichtbar offen und traegt ``@click.away="closeAll"``; das Overlay ist im
//      DOM sein GESCHWISTER, kein Kind. Ein Tap ins Suchfeld lag damit ausserhalb
//      des Dropdowns, feuerte dessen ``click.away`` — und ``closeAll`` setzt
//      ``searchOpen = false``. Das Overlay schloss also genau durch die Geste,
//      mit der man es benutzen wollte.
//   2. Der Fokus hing an ``init``. ``init`` laeuft einmal beim Seitenaufbau,
//      waehrend das Overlay per ``x-show`` + ``x-cloak`` versteckt ist;
//      ``focus()`` auf ein ``display:none``-Element ist wirkungslos, und
//      ``x-show`` re-mountet die Komponente beim Oeffnen nicht (anders als
//      ``x-if``).
//
// Beide E2E-Tests waren gruen, weil keiner einen echten Klick ins Feld absetzt
// (der eine prueft nur Sichtbarkeit, der andere nutzt ``.fill()``).

import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const factories = {};

beforeAll(async () => {
    window.Alpine = {
        data: vi.fn((name, factory) => {
            factories[name] = factory;
        }),
        store: vi.fn(),
    };
    await import("../../src/static/js/alpine/base-layout.js");
    document.dispatchEvent(new Event("alpine:init"));
});

/** Eine ``mobileMore``-Instanz im Ausgangszustand „Mehr-Dropdown offen". */
function openedMore() {
    const component = factories.mobileMore();
    component.toggleMore();
    return component;
}

describe("mobileMore", () => {
    it("registers both mobile components", () => {
        expect(factories.mobileMore).toBeTypeOf("function");
        expect(factories.mobileSearchInput).toBeTypeOf("function");
    });

    it("closes the more dropdown when the search overlay opens", () => {
        const component = openedMore();

        component.toggleSearch();

        expect(component.searchOpen).toBe(true);
        expect(component.moreOpen).toBe(false);
    });

    it("keeps the search overlay alive when the dropdown's click.away fires", () => {
        // Genau die Geste aus dem Befund: Overlay auf, dann Tap ins Suchfeld.
        // ``closeAll`` ist der Handler des Dropdowns — er darf das Overlay nur
        // dann noch treffen koennen, wenn das Dropdown ueberhaupt sichtbar ist.
        const component = openedMore();
        component.toggleSearch();

        expect(component.moreOpen).toBe(false); // -> x-show false -> kein click.away
        expect(component.searchOpen).toBe(true);
    });

    it("still closes both flags from closeAll while the dropdown is open", () => {
        const component = openedMore();

        component.closeAll();

        expect(component.moreOpen).toBe(false);
        expect(component.searchOpen).toBe(false);
    });

    it("toggles the overlay shut again on a second toggleSearch", () => {
        const component = openedMore();
        component.toggleSearch();

        component.toggleSearch();

        expect(component.searchOpen).toBe(false);
    });

    it("closeSearch leaves the more dropdown untouched", () => {
        const component = openedMore();

        component.closeSearch();

        expect(component.moreOpen).toBe(true);
        expect(component.searchOpen).toBe(false);
    });
});

describe("mobileSearchInput", () => {
    let input;
    let component;
    let watchers;

    beforeEach(() => {
        input = document.createElement("input");
        document.body.appendChild(input);
        watchers = {};
        component = factories.mobileSearchInput();
        component.$refs = { input };
        component.$nextTick = (fn) => fn();
        component.$watch = vi.fn((expression, callback) => {
            watchers[expression] = callback;
        });
        vi.spyOn(input, "focus");
    });

    it("does not focus at mount time — the overlay is display:none then", () => {
        component.init();

        expect(input.focus).not.toHaveBeenCalled();
    });

    it("watches searchOpen instead", () => {
        component.init();

        expect(component.$watch).toHaveBeenCalledWith("searchOpen", expect.any(Function));
    });

    it("focuses the field when the overlay opens", () => {
        component.init();

        watchers.searchOpen(true);

        expect(input.focus).toHaveBeenCalledTimes(1);
    });

    it("does not focus when the overlay closes", () => {
        component.init();

        watchers.searchOpen(false);

        expect(input.focus).not.toHaveBeenCalled();
    });

    it("keeps the shared search-field contract", () => {
        // Die Komponente ist weiterhin ein ``searchFieldState`` — der Umbau
        // verlegt nur den Ausloeser, er ersetzt die Suchlogik nicht.
        expect(component.q).toBe("");
        expect(component.ariaExpanded).toBe("false");
    });
});
