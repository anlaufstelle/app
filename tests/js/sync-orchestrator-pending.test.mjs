// Refs #1828/#1838: cover pending sync message registration. base.html
// loads crypto.js, offline-queue.js, and offline-edit.js before
// sync-orchestrator.js. Receivers enter window.syncOrchestratorPending;
// the sync module drains it and replaces it with {push: onMessage} for
// later registrations. Both paths must deliver the same messages.
//
// jsdom has no BroadcastChannel. Install a stub before importing the sync
// module so it opens its actual channel and delivers messages to handlers.
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
    // The sync module opens its channel lazily on the first onMessage call.
    // After draining or registering receivers, exactly one instance exists.
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
