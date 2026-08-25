// Refs #1948: sw-register.js stoesst die Precache-Selbstheilung an — einmal
// pro Seitenladen ein CHECK_PRECACHE an den AKTIVEN Worker. Beim
// Erst-Install (registration.active noch null) passiert bewusst nichts:
// install hat den Precache gerade selbst befuellt.
//
// sw-register.js ist eine IIFE — jeder Fall braucht einen frischen
// Modul-Import (vi.resetModules) mit vorab gesetzten Stubs (Muster
// sw-boot-purge-gate.test.mjs).
import { describe, it, expect, afterEach, vi } from "vitest";

function installServiceWorkerStub(activeWorker) {
    const registration = {
        waiting: null,
        active: activeWorker,
        addEventListener: () => {},
    };
    Object.defineProperty(window.navigator, "serviceWorker", {
        configurable: true,
        value: {
            controller: null,
            addEventListener: () => {},
            register: () => Promise.resolve(registration),
        },
    });
}

async function freshLoad(activeWorker) {
    vi.resetModules();
    installServiceWorkerStub(activeWorker);
    window.crypto_session = { awaitReady: async () => {}, hasSessionKey: () => false };
    await import("../../src/static/js/sw-register.js");
    await new Promise((resolve) => setTimeout(resolve, 10));
}

afterEach(() => {
    delete window.crypto_session;
    vi.restoreAllMocks();
});

describe("#1948 — CHECK_PRECACHE-Anstoss beim Seitenladen", () => {
    it("sendet CHECK_PRECACHE an den aktiven Worker (Bestandsgeraet)", async () => {
        const postMessage = vi.fn();
        await freshLoad({ postMessage });

        expect(postMessage).toHaveBeenCalledWith({ type: "CHECK_PRECACHE" });
        expect(postMessage).toHaveBeenCalledTimes(1);
    });

    it("Erst-Install (kein aktiver Worker): kein Send, kein Crash", async () => {
        await expect(freshLoad(null)).resolves.toBeUndefined();
    });
});
