// Refs #1059 (A11Y-015/028/029/040): Die vier Dropdown-Komponenten meldeten
// ihren Zustand nicht (`aria-expanded`), kannten kein Escape und liessen den
// Fokus nach dem Schliessen im Nichts. Dieser Test pinnt die drei Zusagen je
// Komponente: String-Getter fuer `:aria-expanded` (CSP-Build vertraegt keine
// Inline-Ausdruecke), `closeWithFocus()` gibt den Fokus an `x-ref="trigger"`
// zurueck, und `close()` (an `@click.away`) tut das bewusst NICHT.
import { beforeAll, describe, expect, it, vi } from "vitest";

const factories = {};

beforeAll(async () => {
    window.Alpine = {
        data: vi.fn((name, factory) => {
            factories[name] = factory;
        }),
        store: vi.fn(),
    };
    await import("../../src/static/js/alpine/base-layout.js");
    await import("../../src/static/js/alpine/widgets.js");
    document.dispatchEvent(new Event("alpine:init"));
});

/** Baut einen echten, fokussierbaren Trigger im jsdom-Dokument. */
function triggerButton() {
    const button = document.createElement("button");
    document.body.appendChild(button);
    return button;
}

const CASES = [
    {
        name: "createMenu",
        openProp: "createOpen",
        open: "toggle",
        closeWithFocus: "closeWithFocus",
        expanded: "ariaExpanded",
        ref: "trigger",
    },
    {
        name: "mobileOverflowMenu",
        openProp: "mobileMenu",
        open: "toggle",
        closeWithFocus: "closeWithFocus",
        expanded: "ariaExpanded",
        ref: "trigger",
    },
    {
        name: "simpleDropdown",
        openProp: "open",
        open: "toggle",
        closeWithFocus: "closeWithFocus",
        expanded: "ariaExpanded",
        ref: "trigger",
    },
    {
        name: "mobileMore",
        openProp: "moreOpen",
        open: "toggleMore",
        closeWithFocus: "closeMoreWithFocus",
        expanded: "ariaMoreExpanded",
        ref: "moreTrigger",
    },
];

describe.each(CASES)("$name", (spec) => {
    function build() {
        const component = factories[spec.name]();
        const button = triggerButton();
        component.$refs = { [spec.ref]: button };
        return { component, button };
    }

    it("meldet den Zustand als String fuer :aria-expanded", () => {
        const { component } = build();

        expect(component[spec.expanded]).toBe("false");
        component[spec.open]();
        expect(component[spec.expanded]).toBe("true");
    });

    it("gibt den Fokus nach Escape an den Trigger zurueck", () => {
        const { component, button } = build();
        component[spec.open]();

        component[spec.closeWithFocus]();

        expect(component[spec.openProp]).toBe(false);
        expect(document.activeElement).toBe(button);
    });

    it("laesst den Fokus in Ruhe, wenn das Menue bereits zu ist", () => {
        const { component, button } = build();
        const elsewhere = triggerButton();
        elsewhere.focus();

        component[spec.closeWithFocus]();

        expect(document.activeElement).toBe(elsewhere);
        expect(document.activeElement).not.toBe(button);
    });

    it("holt den Fokus bei click.away (close) NICHT zurueck", () => {
        const { component, button } = build();
        component[spec.open]();
        const elsewhere = triggerButton();
        elsewhere.focus();

        // ``mobileMore`` schliesst per ``closeAll`` (Dropdown + Overlay).
        if (typeof component.close === "function") {
            component.close();
        } else {
            component.closeAll();
        }

        expect(component[spec.openProp]).toBe(false);
        expect(document.activeElement).toBe(elsewhere);
        expect(document.activeElement).not.toBe(button);
    });

    it("ueberlebt einen fehlenden x-ref", () => {
        const component = factories[spec.name]();
        component.$refs = {};
        component[spec.open]();

        expect(() => component[spec.closeWithFocus]()).not.toThrow();
        expect(component[spec.openProp]).toBe(false);
    });
});

describe("mobileMore: Such-Overlay", () => {
    it("meldet den Overlay-Zustand getrennt vom Dropdown", () => {
        const component = factories.mobileMore();

        expect(component.ariaSearchExpanded).toBe("false");
        component.toggleSearch();
        expect(component.ariaSearchExpanded).toBe("true");
        // Refs #1861: das Dropdown geht zu, sobald die Suche aufgeht.
        expect(component.ariaMoreExpanded).toBe("false");
    });

    it("reisst den Fokus bei Escape nicht aus dem offenen Suchfeld", () => {
        const component = factories.mobileMore();
        const button = triggerButton();
        component.$refs = { moreTrigger: button };
        const searchInput = triggerButton();
        component.toggleSearch();
        searchInput.focus();

        component.closeMoreWithFocus();

        expect(document.activeElement).toBe(searchInput);
    });
});

describe("historyEntryDetails", () => {
    it("meldet den Zustand des Diff-Blocks als String", () => {
        const component = factories.historyEntryDetails();

        expect(component.ariaExpanded).toBe("false");
        expect(component.isClosed).toBe(true);
        component.toggle();
        expect(component.ariaExpanded).toBe("true");
        expect(component.isClosed).toBe(false);
    });
});

describe("mobileMore: focused rows stay visible", () => {
    it("scrolls the focused last action into the panel viewport", () => {
        const component = factories.mobileMore();
        const button = triggerButton();
        button.scrollIntoView = vi.fn();
        component.toggleMore();
        component.revealFocusedItem({ target: button });
        expect(button.scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
    });
});
