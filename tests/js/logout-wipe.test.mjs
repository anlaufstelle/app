// Refs #1476 (R2): Der Logout-Submit-Listener in sw-register.js muss den
// asynchronen Offline-Wipe (CryptoKey + Dexie-Purge) VOR dem nativen
// Logout-POST fertig laufen lassen. Vorher war der Wipe fire-and-forget: der
// POST rannte gegen den async Purge, und auf Browsern ohne
// Clear-Site-Data-Support ueberlebten Schluessel + PII sichtbar auf der
// oeffentlichen /offline/-Seite.
//
// sw-register.js ist eine IIFE, die sich beim Laden einen ``submit``-Listener
// an ``document`` haengt. jsdom implementiert ``form.submit()`` nicht — wir
// ersetzen es pro Form durch einen Spy und pruefen die Serialisierung ueber
// die Aufrufreihenfolge (Wipe erst, Submit danach).
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from "vitest";

beforeAll(async () => {
    // ``serviceWorker`` fehlt in jsdom -> der Registrierungsblock wird
    // uebersprungen; nur der Submit-Listener + Boot-Purge werden verdrahtet.
    await import("../../src/static/js/sw-register.js");
});

afterEach(() => {
    delete window.crypto_session;
    delete window.offlineStore;
    document.body.innerHTML = "";
    vi.restoreAllMocks();
});

function makeLogoutForm(action = "/logout/") {
    const form = document.createElement("form");
    form.setAttribute("action", action);
    // jsdom-Stub: das echte HTMLFormElement.submit() ist nicht implementiert.
    form.submit = vi.fn();
    document.body.appendChild(form);
    return form;
}

function dispatchSubmit(form) {
    const ev = new Event("submit", { bubbles: true, cancelable: true });
    form.dispatchEvent(ev);
    return ev;
}

describe("Logout-Wipe-Serialisierung", () => {
    it("haelt den nativen POST an und schickt erst nach dem Wipe ab", async () => {
        let resolveWipe;
        const wipe = vi.fn(
            () =>
                new Promise((res) => {
                    resolveWipe = res;
                })
        );
        window.crypto_session = { wipeOfflineState: wipe };

        const form = makeLogoutForm();
        const ev = dispatchSubmit(form);

        // Der native Logout-POST ist unterbunden ...
        expect(ev.defaultPrevented).toBe(true);
        // ... der Wipe laeuft ...
        expect(wipe).toHaveBeenCalledTimes(1);
        // ... und solange er nicht fertig ist, wird NICHT abgeschickt.
        await Promise.resolve();
        expect(form.submit).not.toHaveBeenCalled();

        // Wipe fertig -> jetzt (und erst jetzt) der programmatische Submit.
        resolveWipe();
        await vi.waitFor(() => expect(form.submit).toHaveBeenCalledTimes(1));
    });

    it("schickt den Logout auch ab, wenn der Wipe wirft", async () => {
        window.crypto_session = {
            wipeOfflineState: vi.fn(() => Promise.reject(new Error("boom"))),
        };
        const form = makeLogoutForm();
        dispatchSubmit(form);
        await vi.waitFor(() => expect(form.submit).toHaveBeenCalledTimes(1));
    });

    it("bleibt nie am Wipe haengen (harter Timeout-Fallback)", async () => {
        vi.useFakeTimers();
        try {
            // Wipe, der nie aufloest.
            window.crypto_session = { wipeOfflineState: vi.fn(() => new Promise(() => {})) };
            const form = makeLogoutForm();
            dispatchSubmit(form);
            expect(form.submit).not.toHaveBeenCalled();

            await vi.advanceTimersByTimeAsync(3000);
            expect(form.submit).toHaveBeenCalledTimes(1);
        } finally {
            vi.useRealTimers();
        }
    });

    it("schickt sofort ab, wenn keine Offline-Stores geladen sind", async () => {
        const form = makeLogoutForm();
        dispatchSubmit(form);
        await vi.waitFor(() => expect(form.submit).toHaveBeenCalledTimes(1));
    });

    it("faellt auf offlineStore.purgeAll() zurueck, wenn crypto_session fehlt", async () => {
        const purgeAll = vi.fn(() => Promise.resolve());
        window.offlineStore = { purgeAll };
        const form = makeLogoutForm();
        dispatchSubmit(form);
        await vi.waitFor(() => expect(form.submit).toHaveBeenCalledTimes(1));
        expect(purgeAll).toHaveBeenCalledTimes(1);
    });

    it("ignoriert Nicht-Logout-Formulare", async () => {
        const wipe = vi.fn(() => Promise.resolve());
        window.crypto_session = { wipeOfflineState: wipe };
        const form = makeLogoutForm("/clients/create/");
        const ev = dispatchSubmit(form);
        await Promise.resolve();
        expect(ev.defaultPrevented).toBe(false);
        expect(wipe).not.toHaveBeenCalled();
        expect(form.submit).not.toHaveBeenCalled();
    });

    it("unterbindet einen zweiten Logout-Klick auf dieselbe Form (Re-Entrancy)", async () => {
        let resolveWipe;
        const wipe = vi.fn(
            () =>
                new Promise((res) => {
                    resolveWipe = res;
                })
        );
        window.crypto_session = { wipeOfflineState: wipe };

        const form = makeLogoutForm();
        dispatchSubmit(form);
        // Zweiter Klick, waehrend der Wipe noch laeuft.
        const ev2 = dispatchSubmit(form);
        expect(ev2.defaultPrevented).toBe(true);
        expect(wipe).toHaveBeenCalledTimes(1);

        resolveWipe();
        await vi.waitFor(() => expect(form.submit).toHaveBeenCalledTimes(1));
    });
});
