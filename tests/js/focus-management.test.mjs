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
        // Auch das Opt-in-Ziel bleibt in der Tab-Reihenfolge.
        expect(document.getElementById("opt-in").hasAttribute("tabindex")).toBe(false);
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

    it("nimmt das invalide Feld NICHT aus der Tab-Reihenfolge", () => {
        // Refs #1059 (Cross-Review B2', HOCH): ein programmatisch gesetztes
        // tabindex="-1" haette das Feld nach dem Verlassen unerreichbar
        // gemacht — native Controls sind ohnehin fokussierbar.
        const target = container('<input id="erstes" aria-invalid="true">');

        afterSwap(target);

        const feld = document.getElementById("erstes");
        expect(document.activeElement).toBe(feld);
        expect(feld.hasAttribute("tabindex")).toBe(false);
    });

    it("erkennt ein invalides Feld, das der Container SELBST ist (outerHTML-Swap)", () => {
        const field = document.createElement("input");
        field.id = "feld";
        field.setAttribute("aria-invalid", "true");
        document.body.appendChild(field);

        afterSwap(field);

        expect(document.activeElement.id).toBe("feld");
        expect(field.hasAttribute("tabindex")).toBe(false);
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

// Die vormalige "Trefferansage (A11Y-035/036)"-Testgruppe pinnte
// ``_announceResultCount`` (DOM-Zeilenzaehlung ueber
// ``data-hx-announce-*``-Attribute). Refs #2155: der Pfad ist entfernt — die
// Trefferansage kommt jetzt serverseitig als ``hx-swap-oob``-Fragment aus den
// Listen-Templates selbst (echter ``paginator.count`` statt gezaehlter
// DOM-Zeilen). Die beiden hier mitgeprueften, weiterhin gueltigen Verhalten
// (``data-hx-no-focus`` unterdrueckt Fokus, Live-Suche behaelt den Fokus im
// Suchfeld) bleiben oben in "Die vorgelagerten Ausstiege bleiben
// unveraendert" abgedeckt — unabhaengig von der Ansage getestet.
