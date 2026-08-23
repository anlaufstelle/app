// Refs #1966: Der Boot-Purge darf nicht an ``data-user-id`` haengen.
//
// sw-register.js raeumt beim Laden abgelaufene IndexedDB-Records weg (TTL).
// Das Gate war ``window.offlineStore && document.body.dataset.userId`` — ein
// Auth-Signal, das aus dem Markup kam. Die precachten Offline-Shells tragen
// seit #1966 kein ``data-user-id`` mehr (es fror die Nutzer-pk im
// Service-Worker-Cache ein). Ein PWA-Kaltstart direkt in eine Shell haette den
// Purge damit still uebersprungen und abgelaufene, verschluesselte Bestaende
// laenger liegen lassen als die Aufbewahrung erlaubt.
//
// Das echte Gate ist der Session-Key (``crypto_session.hasSessionKey``) — ohne
// ihn gibt es ohnehin keine Loeschentscheidung (Refs #1352). Es bleibt
// unangetastet; nur die Markup-Abhaengigkeit faellt weg.
import { describe, it, expect, afterEach, vi } from "vitest";

function installServiceWorkerStub() {
    const registration = { waiting: null, addEventListener: () => {} };
    Object.defineProperty(window.navigator, "serviceWorker", {
        configurable: true,
        value: {
            controller: null,
            addEventListener: () => {},
            register: () => Promise.resolve(registration),
        },
    });
}

/** sw-register.js frisch laden, Boot-Hook abarbeiten lassen. */
async function freshLoad({ userId, hasSessionKey }) {
    vi.resetModules();
    document.body.innerHTML = "";
    if (userId === undefined) {
        delete document.body.dataset.userId;
    } else {
        document.body.dataset.userId = userId;
    }
    installServiceWorkerStub();

    const purgeExpired = vi.fn(async () => {});
    window.offlineStore = { purgeExpired };
    window.crypto_session = {
        ready: async () => {},
        hasSessionKey: () => hasSessionKey,
    };

    await import("../../src/static/js/sw-register.js");
    await new Promise((resolve) => setTimeout(resolve, 10));
    return purgeExpired;
}

afterEach(() => {
    delete window.crypto_session;
    delete window.offlineStore;
    delete document.body.dataset.userId;
    document.body.innerHTML = "";
    vi.restoreAllMocks();
});

describe("#1966 Boot-Purge haengt am Session-Key, nicht am Markup", () => {
    it("purgt auf einer Offline-Shell OHNE data-user-id", async () => {
        const purgeExpired = await freshLoad({ userId: undefined, hasSessionKey: true });

        expect(purgeExpired).toHaveBeenCalledTimes(1);
    });

    it("purgt weiterhin auf einer normalen Seite MIT data-user-id", async () => {
        const purgeExpired = await freshLoad({ userId: "42", hasSessionKey: true });

        expect(purgeExpired).toHaveBeenCalledTimes(1);
    });

    it("purgt NICHT ohne Session-Key (Idle-Lock / vor dem Re-Login, Refs #1352)", async () => {
        const purgeExpired = await freshLoad({ userId: "42", hasSessionKey: false });

        expect(purgeExpired).not.toHaveBeenCalled();
    });
});
