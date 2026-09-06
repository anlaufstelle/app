// Refs #1059: Der HTMX-Fokus-Manager war zweistufig (Opt-in-Element ODER
// Swap-Container). Die im Ticket beschriebene mittlere Stufe — "sonst das
// erste invalide Feld" — fehlte: nach einem Server-Re-Render mit
// Validierungsfehlern landete der Fokus auf dem Container, und der Fehler
// selbst war nur durch Weiterlesen zu finden.
//
// Dieser Test pinnt alle drei Stufen in ihrer Reihenfolge sowie die beiden
// vorgelagerten Ausstiege (Opt-out, Live-Such-Schutz), damit die neue Stufe
// die bestehenden Zusagen nicht verschiebt.
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

beforeAll(async () => {
    await import("../../src/static/js/focus-management.js");
});

beforeEach(() => {
    document.body.innerHTML = "";
    document.body.removeAttribute("tabindex");
});

/** Feuert ``htmx:afterSwap`` so, wie htmx es tut: am body, mit ``detail.target``. */
function afterSwap(target) {
    document.body.dispatchEvent(
        new CustomEvent("htmx:afterSwap", { bubbles: true, detail: { target } })
    );
}

function container(html, attrs = {}) {
    const div = document.createElement("div");
    div.id = "swap-target";
    Object.entries(attrs).forEach(([key, value]) => div.setAttribute(key, value));
    div.innerHTML = html;
    document.body.appendChild(div);
    return div;
}

describe("Stufe 1 — explizites Opt-in", () => {
    it("fokussiert [data-hx-focus] vor allem anderen", () => {
        const target = container(
            '<input id="invalid" aria-invalid="true"><input id="opt-in" data-hx-focus>'
        );

        afterSwap(target);

        expect(document.activeElement.id).toBe("opt-in");
    });

    it("fokussiert [autofocus] ebenso", () => {
        const target = container('<input id="auto" autofocus><input id="invalid" aria-invalid="true">');

        afterSwap(target);

        expect(document.activeElement.id).toBe("auto");
    });
});

describe("Stufe 2 — erstes invalides Feld", () => {
    it("fokussiert das erste aria-invalid-Feld, wenn kein Opt-in existiert", () => {
        const target = container(
            '<input id="ok"><input id="erstes" aria-invalid="true"><input id="zweites" aria-invalid="true">'
        );

        afterSwap(target);

        expect(document.activeElement.id).toBe("erstes");
    });

    it("erkennt ein invalides Feld, das der Container SELBST ist (outerHTML-Swap)", () => {
        const field = document.createElement("input");
        field.id = "feld";
        field.setAttribute("aria-invalid", "true");
        document.body.appendChild(field);

        afterSwap(field);

        expect(document.activeElement.id).toBe("feld");
    });

    it("ignoriert aria-invalid=false", () => {
        const target = container('<input id="sauber" aria-invalid="false">');

        afterSwap(target);

        expect(document.activeElement.id).toBe("swap-target");
    });
});

describe("Stufe 3 — Container-Fallback", () => {
    it("fokussiert den Swap-Container und macht ihn dafuer fokussierbar", () => {
        const target = container("<p>Neuer Inhalt</p>");

        afterSwap(target);

        expect(document.activeElement.id).toBe("swap-target");
        expect(target.getAttribute("tabindex")).toBe("-1");
    });

    it("laesst ein vorhandenes tabindex unangetastet", () => {
        const target = container("<p>x</p>", { tabindex: "0" });

        afterSwap(target);

        expect(target.getAttribute("tabindex")).toBe("0");
    });
});

describe("Die vorgelagerten Ausstiege bleiben unveraendert", () => {
    it("data-hx-no-focus am Swap-Ziel unterdrueckt auch Stufe 2", () => {
        const target = container('<input id="invalid" aria-invalid="true">', {
            "data-hx-no-focus": "",
        });

        afterSwap(target);

        expect(document.activeElement).toBe(document.body);
    });

    it("Live-Suche: ein fokussiertes Textfeld ausserhalb des Ziels behaelt den Fokus", () => {
        const suche = document.createElement("input");
        suche.type = "search";
        suche.id = "suche";
        document.body.appendChild(suche);
        const target = container('<input id="invalid" aria-invalid="true">');
        suche.focus();

        afterSwap(target);

        expect(document.activeElement.id).toBe("suche");
    });

    it("laesst einen Fokus in Ruhe, der bereits IM getauschten Ziel liegt", () => {
        const target = container('<button id="drin"></button><input id="invalid" aria-invalid="true">');
        document.getElementById("drin").focus();

        afterSwap(target);

        expect(document.activeElement.id).toBe("drin");
    });

    it("greift einen outerHTML-Swap ueber die id wieder auf", () => {
        const alt = container('<input id="invalid" aria-invalid="true">');
        alt.remove();
        const neu = container('<input id="invalid" aria-invalid="true">');

        afterSwap(alt);

        expect(neu.contains(document.activeElement)).toBe(true);
        expect(document.activeElement.id).toBe("invalid");
    });
});

describe("Trefferansage (A11Y-035/036)", () => {
    function liste(rows, attrs = {}) {
        const region = document.createElement("p");
        region.id = "liste-status";
        region.setAttribute("role", "status");
        region.textContent = "alt";
        document.body.appendChild(region);

        const target = container(
            Array.from({ length: rows }, () => '<div data-testid="zeile"></div>').join(""),
            Object.assign(
                {
                    "data-hx-no-focus": "",
                    "data-hx-announce-region": "liste-status",
                    "data-hx-announce-rows": "[data-testid='zeile']",
                    "data-hx-announce-none": "Keine Person gefunden.",
                    "data-hx-announce-one": "1 Person angezeigt.",
                    "data-hx-announce-many": "{count} Personen angezeigt.",
                },
                attrs
            )
        );
        return { region, target };
    }

    it.each([
        [0, "Keine Person gefunden."],
        [1, "1 Person angezeigt."],
        [7, "7 Personen angezeigt."],
    ])("sagt bei %i Zeilen %s an", (rows, erwartet) => {
        const { region, target } = liste(rows);

        afterSwap(target);

        expect(region.textContent).toBe(erwartet);
    });

    it("sagt an, obwohl data-hx-no-focus den Fokus unterdrueckt", () => {
        const { region, target } = liste(3);

        afterSwap(target);

        expect(region.textContent).toBe("3 Personen angezeigt.");
        expect(document.activeElement).toBe(document.body);
    });

    it("sagt auch bei laufender Live-Suche an (Fokus bleibt im Suchfeld)", () => {
        const suche = document.createElement("input");
        suche.type = "search";
        document.body.appendChild(suche);
        const { region, target } = liste(2);
        suche.focus();

        afterSwap(target);

        expect(region.textContent).toBe("2 Personen angezeigt.");
        expect(document.activeElement).toBe(suche);
    });

    it("ersetzt den Textknoten auch bei gleicher Trefferzahl", () => {
        const { region, target } = liste(2);
        afterSwap(target);
        const erster = region.firstChild;

        afterSwap(target);

        expect(region.textContent).toBe("2 Personen angezeigt.");
        expect(region.firstChild).not.toBe(erster);
    });

    it("laesst Swaps ohne Ansage-Attribute unberuehrt", () => {
        const region = document.createElement("p");
        region.id = "liste-status";
        region.textContent = "unveraendert";
        document.body.appendChild(region);
        const target = container('<div data-testid="zeile"></div>');

        afterSwap(target);

        expect(region.textContent).toBe("unveraendert");
    });

    it("bleibt still, wenn die Ansagezeile fehlt", () => {
        const target = container('<div data-testid="zeile"></div>', {
            "data-hx-announce-region": "gibts-nicht",
            "data-hx-announce-rows": "[data-testid='zeile']",
            "data-hx-announce-one": "1 Person angezeigt.",
        });

        expect(() => afterSwap(target)).not.toThrow();
    });
});
