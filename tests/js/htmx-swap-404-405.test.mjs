// Refs #2321 (H-4): 404 und 405 duerfen das HTMX-Ziel nicht einwechseln.
//
// Seit Refs #2232 gilt fuer 4xx die Regel „swap: true" (fachliche Partials
// wie „Die Schicht konnte nicht bestimmt werden" landen genau dort, wo der
// Nutzer sie erwartet). 404 und 405 sind aber keine fachlichen Partials:
// eine 404 wechselt ein ganzes HTML-Dokument in ein kleines Ziel, eine 405
// hat einen leeren Rumpf und LEERT das Ziel. Kein Datenabfluss, aber ein
// Oberflaechenzustand, aus dem der Nutzer ohne Neuladen nicht herauskommt.
// Behandelt werden sie deshalb wie 403: kein Swap, dafuer der globale Toast.
import { describe, it, expect, beforeAll, beforeEach, afterEach } from "vitest";

const GENERIC = "Die Aktion konnte nicht ausgeführt werden. Bitte erneut versuchen.";

beforeAll(async () => {
    await import("../../src/static/js/htmx-errors.js");
});

function clearToast() {
    const toast = document.getElementById("htmx-error-toast");
    if (toast) toast.remove();
}

beforeEach(clearToast);
afterEach(clearToast);

function beforeSwap(status, { shouldSwap = true, swapOverride = undefined } = {}) {
    const detail = { xhr: { status }, shouldSwap, swapOverride, requestConfig: { elt: document.body } };
    document.body.dispatchEvent(new CustomEvent("htmx:beforeSwap", { detail }));
    return detail;
}

function responseError(status, responseText) {
    document.body.dispatchEvent(
        new CustomEvent("htmx:responseError", { detail: { xhr: { status, responseText } } })
    );
    const toast = document.getElementById("htmx-error-toast");
    return toast ? toast.querySelector("[data-toast-text]").textContent : null;
}

describe("htmx: 404/405 wechseln das Ziel nicht ein", () => {
    it("unterbindet den Swap einer 404", () => {
        expect(beforeSwap(404).shouldSwap).toBe(false);
    });

    it("unterbindet den Swap einer 405", () => {
        expect(beforeSwap(405).shouldSwap).toBe(false);
    });

    it("laesst den fachlichen 4xx-Swap unveraendert", () => {
        expect(beforeSwap(400).shouldSwap).toBe(true);
        expect(beforeSwap(409).shouldSwap).toBe(true);
        expect(beforeSwap(422).shouldSwap).toBe(true);
    });

    it("laesst erfolgreiche Antworten unveraendert", () => {
        expect(beforeSwap(200).shouldSwap).toBe(true);
    });
});

describe("htmx: der Nutzer erfaehrt den Fehlschlag statt ihn zu sehen", () => {
    it("zeigt den Toast, weil die 404 nicht geswappt wurde", () => {
        const xhr = { status: 404, responseText: "<html><body>404</body></html>" };
        const detail = { xhr, shouldSwap: true, requestConfig: { elt: document.body } };
        document.body.dispatchEvent(new CustomEvent("htmx:beforeSwap", { detail }));
        expect(detail.shouldSwap).toBe(false);
        document.body.dispatchEvent(new CustomEvent("htmx:responseError", { detail: { xhr } }));
        const toast = document.getElementById("htmx-error-toast");
        expect(toast).not.toBeNull();
        expect(toast.querySelector("[data-toast-text]").textContent).toBe(GENERIC);
    });

    it("unterdrueckt den Toast weiterhin fuer ein tatsaechlich geswapptes 4xx-Partial", () => {
        const xhr = { status: 409, responseText: "Der Vorschlag wurde bereits entschieden." };
        const detail = { xhr, shouldSwap: true, requestConfig: { elt: document.body } };
        document.body.dispatchEvent(new CustomEvent("htmx:beforeSwap", { detail }));
        expect(detail.shouldSwap).toBe(true);
        document.body.dispatchEvent(new CustomEvent("htmx:responseError", { detail: { xhr } }));
        expect(document.getElementById("htmx-error-toast")).toBeNull();
    });
});

describe("htmx: Regressionsschutz fuer den Toast-Pfad", () => {
    it("zeigt kurze Klartext-Bodies unveraendert an", () => {
        expect(responseError(400, "Begründung ist erforderlich.")).toBe("Begründung ist erforderlich.");
    });
});
