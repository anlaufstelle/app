// Refs #1828/#1838 (Integrator-Auflage): Der Pending-Drain-Pfad der
// Orchestrator-Receiver war bisher ungetestet. base.html laedt crypto.js/
// offline-queue.js/offline-edit.js VOR sync-orchestrator.js — sie legen ihre
// Cross-Tab-Empfaenger in window.syncOrchestratorPending; der Orchestrator
// draint die Liste beim Laden und ersetzt sie durch {push: onMessage}
// (Sofort-Registrierung fuer Nachzuegler). Beide Wege muessen denselben
// Empfangs-Effekt haben.
//
// jsdom hat kein BroadcastChannel — ein Stub VOR dem Import laesst den
// Orchestrator seinen Channel real oeffnen, sodass eingehende Nachrichten
// end-to-end bis zu den registrierten Handlern getrieben werden koennen.
import { afterEach, describe, expect, it, vi } from "vitest";

let _channels;

class FakeBroadcastChannel {
    constructor(name) {
        this.name = name;
        this.onmessage = null;
        _channels.push(this);
    }

    postMessage(_msg) {
        // Bewusst KEIN Selbst-Echo — wie der echte BroadcastChannel.
    }

    close() {}
}

async function loadOrchestrator({ pending } = {}) {
    vi.resetModules();
    _channels = [];
    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
    delete window.syncOrchestrator;
    if (pending === undefined) {
        delete window.syncOrchestratorPending;
    } else {
        window.syncOrchestratorPending = pending;
    }
    await import("../../src/static/js/sync-orchestrator.js");
}

function receiveFromOtherTab(msg) {
    // Der Orchestrator oeffnet seinen Channel lazy beim ersten onMessage —
    // nach Drain/Registrierung existiert genau eine Instanz.
    expect(_channels.length).toBeGreaterThan(0);
    _channels[0].onmessage({ data: msg });
}

afterEach(() => {
    vi.unstubAllGlobals();
    delete window.syncOrchestrator;
    delete window.syncOrchestratorPending;
});

describe("#1828 — window.syncOrchestratorPending (Drain + Uebernahme)", () => {
    it("draint vorab gepushte Empfaenger: sie erhalten Cross-Tab-Nachrichten", async () => {
        const early = vi.fn();
        await loadOrchestrator({ pending: [early] });

        receiveFromOtherTab({ type: "sync-finished" });

        expect(early).toHaveBeenCalledTimes(1);
        expect(early).toHaveBeenCalledWith({ type: "sync-finished" });
    });

    it("uebernimmt die Liste als Sofort-Registrierung fuer Nachzuegler", async () => {
        await loadOrchestrator({ pending: [] });
        const late = vi.fn();
        // Nachzuegler nutzen DASSELBE Idiom wie die Frueh-Registrierer —
        // push registriert jetzt sofort (kein Array-Liegenbleiben).
        window.syncOrchestratorPending.push(late);

        receiveFromOtherTab({ type: "key-cleared" });

        expect(late).toHaveBeenCalledTimes(1);
        expect(late).toHaveBeenCalledWith({ type: "key-cleared" });
        expect(Array.isArray(window.syncOrchestratorPending)).toBe(false);
    });

    it("mehrere gedrainte Empfaenger feuern alle; ein werfender killt die uebrigen nicht", async () => {
        const boom = vi.fn(() => {
            throw new Error("boom");
        });
        const calm = vi.fn();
        await loadOrchestrator({ pending: [boom, calm] });

        receiveFromOtherTab({ type: "sync-finished" });

        expect(boom).toHaveBeenCalledTimes(1);
        expect(calm).toHaveBeenCalledTimes(1);
    });

    it("ohne Vorab-Liste: Uebernahme-Objekt existiert trotzdem (Nachzuegler-Idiom traegt)", async () => {
        await loadOrchestrator();

        expect(window.syncOrchestratorPending).toBeDefined();
        expect(typeof window.syncOrchestratorPending.push).toBe("function");
    });

    it("Doppel-Load (Test-Reset-Szenario): Nicht-Array-Uebernahme wird nicht erneut gedraint", async () => {
        const early = vi.fn();
        await loadOrchestrator({ pending: [early] });
        const takeover = window.syncOrchestratorPending;

        // Zweiter Load sieht das Uebernahme-Objekt statt eines Arrays —
        // Array.isArray-Gate verhindert einen Drain-Versuch darauf.
        vi.resetModules();
        window.syncOrchestratorPending = takeover;
        delete window.syncOrchestrator;
        await import("../../src/static/js/sync-orchestrator.js");

        expect(typeof window.syncOrchestratorPending.push).toBe("function");
    });
});
