// Refs #1873: Der globale HTMX-Fehler-Toast (htmx-errors.js) las bisher
// ausschliesslich den Antwort-Body und nie ``xhr.status``. Ein Rate-Limit
// (429) war damit von einem Serverfehler nicht zu unterscheiden — beide
// zeigten „Die Aktion konnte nicht ausgefuehrt werden.".
//
// Der Regelfall ist heute der uebersetzte Klartext-Body aus ``handler403``;
// die statusabhaengige Meldung ist der Rueckfall fuer bodylose oder
// HTML-behaftete 429er (z. B. aus einem vorgelagerten Reverse-Proxy).
import { describe, it, expect, beforeAll, beforeEach, afterEach } from "vitest";

const GENERIC = "Die Aktion konnte nicht ausgeführt werden. Bitte erneut versuchen.";
const RATE_LIMITED = "Zu viele Anfragen in kurzer Zeit. Bitte kurz warten und es erneut versuchen.";

beforeAll(async () => {
    await import("../../src/static/js/htmx-errors.js");
});

beforeEach(() => {
    const toast = document.getElementById("htmx-error-toast");
    if (toast) toast.remove();
});

afterEach(() => {
    const toast = document.getElementById("htmx-error-toast");
    if (toast) toast.remove();
});

function respond(status, responseText) {
    document.body.dispatchEvent(
        new CustomEvent("htmx:responseError", { detail: { xhr: { status, responseText } } })
    );
    const toast = document.getElementById("htmx-error-toast");
    return toast ? toast.querySelector("[data-toast-text]").textContent : null;
}

describe("htmx-errors: Rate-Limit ist als solches erkennbar", () => {
    it("zeigt den Klartext-Hinweis des Servers bei 429", () => {
        expect(respond(429, RATE_LIMITED)).toBe(RATE_LIMITED);
    });

    it("faellt bei einem bodylosen 429 auf den Rate-Limit-Hinweis zurueck", () => {
        expect(respond(429, "")).toBe(RATE_LIMITED);
    });

    it("faellt bei einer HTML-Fehlerseite mit 429 nicht auf die Serverfehler-Meldung", () => {
        const meldung = respond(429, "<html><body>429 Too Many Requests</body></html>");
        expect(meldung).toBe(RATE_LIMITED);
        expect(meldung).not.toBe(GENERIC);
    });
});

describe("htmx-errors: bestehendes Verhalten bleibt", () => {
    it("zeigt kurze Klartext-Bodies unveraendert an", () => {
        expect(respond(400, "Begründung ist erforderlich.")).toBe("Begründung ist erforderlich.");
    });

    it("faellt bei einer HTML-Fehlerseite mit 500 auf die generische Meldung", () => {
        expect(respond(500, "<html><body>Server Error</body></html>")).toBe(GENERIC);
    });

    it("faellt bei einer Antwort ohne xhr auf die generische Meldung", () => {
        document.body.dispatchEvent(new CustomEvent("htmx:responseError", { detail: {} }));
        expect(document.getElementById("htmx-error-toast").querySelector("[data-toast-text]").textContent).toBe(
            GENERIC
        );
    });

    it("zeigt keinen zusaetzlichen Toast fuer eine sichtbar eingewechselte 4xx-Antwort", () => {
        const xhr = { status: 400, responseText: "Validierungsfehler" };
        const target = document.createElement("div");
        const detail = { xhr, target, shouldSwap: true, swapOverride: "outerHTML" };
        let localHandlerCalls = 0;
        const localHandler = () => {
            localHandlerCalls += 1;
        };
        document.body.addEventListener("htmx:responseError", localHandler);

        document.body.dispatchEvent(new CustomEvent("htmx:beforeSwap", { detail }));
        document.body.dispatchEvent(new CustomEvent("htmx:responseError", { detail: { xhr } }));
        document.body.removeEventListener("htmx:responseError", localHandler);

        expect(document.getElementById("htmx-error-toast")).toBeNull();
        expect(detail.target).toBe(target);
        expect(localHandlerCalls).toBe(1);
    });

    it("behaelt den Fehler-Toast wenn hx-swap none keinen Inhalt sichtbar macht", () => {
        const xhr = { status: 404, responseText: "" };

        document.body.dispatchEvent(
            new CustomEvent("htmx:beforeSwap", {
                detail: { xhr, shouldSwap: true, swapOverride: "none" },
            })
        );
        document.body.dispatchEvent(new CustomEvent("htmx:responseError", { detail: { xhr } }));

        expect(document.getElementById("htmx-error-toast")).not.toBeNull();
    });
});
