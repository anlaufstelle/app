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
import { IDBFactory } from "fake-indexeddb";

beforeAll(async () => {
    // ``serviceWorker`` fehlt in jsdom -> der Registrierungsblock wird
    // uebersprungen; nur der Submit-Listener + Boot-Purge werden verdrahtet.
    await import("../../src/static/js/sw-register.js");
});

afterEach(() => {
    delete window.crypto_session;
    delete window.offlineStore;
    // Refs #1951: Der Logout-Handler setzt diese Marke und nimmt sie
    // absichtlich nie zurueck. Ohne Aufraeumen wuerde eine spaeter hier
    // ergaenzte Reload-Assertion still unterdrueckt.
    delete window.__logoutInFlight;
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

// ---------------------------------------------------------------------------
// Refs #1946: Woher der `lastActivity`-Eintrag nach dem Logout WIRKLICH kommt.
//
// Der RC-Lauf 2026-08-18 (ENT-OFFL-14) hat nach dem Abmelden einen Eintrag
// `{key: "lastActivity", ts: <epoch ms>}` in `anlaufstelle-crypto.meta`
// gesehen und daraus geschlossen, der Stempel ueberlebe den Wipe. Das ist am
// Code falsch: `clearSessionKey()` loescht ihn seit #1065 zusammen mit dem
// Schluessel, und `wipeOfflineState()` beginnt mit genau diesem Aufruf.
//
// Die Quelle ist der Boot-Zweig des Idle-Gates: nach dem Logout landet der
// Browser auf `login.html`, die crypto.js ebenfalls laedt. Deren eager
// `_loadKey()` findet KEINEN Stempel vor und legt einen NEUEN an ("No stamp
// yet -> start the idle window now"). Der beobachtete Zeitstempel gehoert
// also zum Aufruf der Anmeldeseite, nicht zur letzten Nutzung der App.
//
// Diese drei Tests nageln beide Halbsaetze fest: der Wipe raeumt vollstaendig
// ab, und der Folge-Boot stempelt neu.
const CRYPTO_DB_NAME = "anlaufstelle-crypto";
const CRYPTO_META_TABLE = "meta";
const CRYPTO_KEY_NAME = "sessionKey";
const CRYPTO_ACTIVITY_KEY = "lastActivity";

function _openCryptoDb() {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(CRYPTO_DB_NAME, 1);
        req.onupgradeneeded = () => {
            const db = req.result;
            if (!db.objectStoreNames.contains(CRYPTO_META_TABLE)) {
                db.createObjectStore(CRYPTO_META_TABLE, { keyPath: "key" });
            }
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
}

function _metaPut(record) {
    return _openCryptoDb().then(
        (db) =>
            new Promise((resolve, reject) => {
                const tx = db.transaction(CRYPTO_META_TABLE, "readwrite");
                tx.objectStore(CRYPTO_META_TABLE).put(record);
                tx.oncomplete = () => {
                    db.close();
                    resolve();
                };
                tx.onerror = () => {
                    db.close();
                    reject(tx.error);
                };
            })
    );
}

function _metaGet(key) {
    return _openCryptoDb().then(
        (db) =>
            new Promise((resolve, reject) => {
                const tx = db.transaction(CRYPTO_META_TABLE, "readonly");
                const req = tx.objectStore(CRYPTO_META_TABLE).get(key);
                req.onsuccess = () => {
                    db.close();
                    resolve(req.result || null);
                };
                req.onerror = () => {
                    db.close();
                    reject(req.error);
                };
            })
    );
}

// Laedt crypto.js frisch (die IIFE haengt sich neu ans window) — das
// entspricht einem Seiten-Boot.
async function bootCryptoJs() {
    vi.resetModules();
    delete window.crypto_session;
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.ready();
}

describe("lastActivity nach dem Logout (Refs #1946)", () => {
    beforeEach(() => {
        // Frische IndexedDB pro Test — der IIFE-Modulzustand ist sonst geteilt.
        const factory = new IDBFactory();
        globalThis.indexedDB = factory;
        window.indexedDB = factory;
    });

    it("raeumt Schluessel UND Aktivitaets-Stempel ab (die Ticket-Praemisse ist widerlegt)", async () => {
        await _metaPut({ key: CRYPTO_KEY_NAME, cryptoKey: { marker: "angemeldet" } });
        await _metaPut({ key: CRYPTO_ACTIVITY_KEY, ts: Date.now() });
        await bootCryptoJs();

        // Genau das ruft der Logout-Hook in sw-register.js auf.
        await window.crypto_session.wipeOfflineState();

        expect(await _metaGet(CRYPTO_KEY_NAME)).toBeNull();
        expect(await _metaGet(CRYPTO_ACTIVITY_KEY)).toBeNull();
    });

    it("legt den Stempel beim Boot einer schluessellosen Seite NEU an (login.html)", async () => {
        // Zustand unmittelbar nach dem Logout-Wipe: leerer meta-Store.
        await bootCryptoJs();

        const stamp = await vi.waitFor(async () => {
            const row = await _metaGet(CRYPTO_ACTIVITY_KEY);
            expect(row).not.toBeNull();
            return row;
        });
        expect(typeof stamp.ts).toBe("number");
        // Ohne Anmeldung entsteht KEIN Schluessel — der Stempel steht allein.
        expect(await _metaGet(CRYPTO_KEY_NAME)).toBeNull();
    });

    it("reproduziert den RC-Befund: der Eintrag nach dem Logout ist juenger als der Wipe", async () => {
        await _metaPut({ key: CRYPTO_KEY_NAME, cryptoKey: { marker: "angemeldet" } });
        await _metaPut({ key: CRYPTO_ACTIVITY_KEY, ts: Date.now() - 60_000 });
        await bootCryptoJs();

        await window.crypto_session.wipeOfflineState();
        const wipedAt = Date.now();
        expect(await _metaGet(CRYPTO_ACTIVITY_KEY)).toBeNull();

        // Navigation auf die Anmeldeseite: crypto.js laeuft dort erneut.
        await bootCryptoJs();

        const stamp = await vi.waitFor(async () => {
            const row = await _metaGet(CRYPTO_ACTIVITY_KEY);
            expect(row).not.toBeNull();
            return row;
        });
        expect(stamp.ts).toBeGreaterThanOrEqual(wipedAt);
    });
});
