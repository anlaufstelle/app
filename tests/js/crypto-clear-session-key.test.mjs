// Refs #2136 (R-L3-13): Der Loeschvertrag von ``crypto_session``.
//
// Warum diese Datei neben ``quick-exit.test.mjs`` steht: Dort spiegeln
// Test-Doubles den Vertrag; hier wird der Vertrag selbst am ECHTEN crypto.js
// gemessen — inklusive des Falls, den das Re-Review aufgedeckt hat.
//
// Der Befund: ``clearSessionKey()`` fing die Fehler beider IndexedDB-Deletes ab
// und erfuellte sein Promise trotzdem. Ein Aufrufer konnte einen Fehlschlag
// nicht von einem Erfolg unterscheiden — und "Schnell verlassen" leitete
// daraus einen ``key-cleared``-Broadcast ab, der in einem Fremd-Tab den
// Schluessel ueber ``_applyKeyCleared`` sogar wieder verankert haette. Fuer den
// Logout-Normalfall war das folgenlos (``Clear-Site-Data`` raeumt ohnehin auf),
// fuer den Offline-Quick-Exit ist es der einzige Riegel.
//
// crypto.js wird EINMAL importiert (es haengt Intervalle und Listener an);
// gesteuert wird stattdessen ``globalThis.indexedDB`` — ``_openDb`` liest die
// Bindung bei jedem Aufruf neu.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const ECHTES_IDB = globalThis.indexedDB; // fake-indexeddb aus setup.mjs
const SALT = "YWJjZGVmZ2hpamtsbW5vcA"; // 16 Byte, base64url — wie in der E2E-Suite

let cryptoSession;

beforeAll(async () => {
    await import("../../src/static/js/crypto.js");
    cryptoSession = window.crypto_session;
});

beforeEach(() => {
    _setIndexedDb(ECHTES_IDB);
});

afterEach(() => {
    _setIndexedDb(ECHTES_IDB);
    vi.restoreAllMocks();
});

function _setIndexedDb(value) {
    Object.defineProperty(globalThis, "indexedDB", { configurable: true, writable: true, value });
    if (typeof window !== "undefined" && window !== globalThis) {
        Object.defineProperty(window, "indexedDB", { configurable: true, writable: true, value });
    }
}

/**
 * Minimale IndexedDB-Attrappe in der Form, die ``crypto.js`` benutzt
 * (``open`` → ``req.onsuccess`` → ``db.transaction(store, mode)`` →
 * ``tx.objectStore(store)``). Je nach Schalter scheitert der Delete oder der
 * Get, waehrend der Rest normal antwortet.
 */
function kaputtesIndexedDb({
    deleteFails = false,
    getFails = false,
    vorhandeneZeile = null,
    // R-L3-14: nur DIESE Schluessel scheitern beim Delete; alle uebrigen
    // gelingen. So laesst sich der partielle Fall stellen — Key-Delete kaputt,
    // Stempel-Delete waere moeglich.
    deleteFailsFor = null,
    protokoll = null,
} = {}) {
    const _scheitert = (key) => (deleteFailsFor ? deleteFailsFor.indexOf(key) !== -1 : deleteFails);
    return {
        open() {
            const req = {};
            queueMicrotask(() => {
                req.result = {
                    objectStoreNames: { contains: () => true },
                    createObjectStore() {},
                    close() {},
                    transaction() {
                        const tx = { error: new Error("IndexedDB-Fehler (Attrappe)") };
                        tx.objectStore = () => ({
                            delete(key) {
                                if (protokoll) protokoll.push(key);
                                queueMicrotask(() => {
                                    if (_scheitert(key)) tx.onerror && tx.onerror();
                                    else tx.oncomplete && tx.oncomplete();
                                });
                            },
                            get() {
                                const anfrage = { error: new Error("Lesefehler (Attrappe)") };
                                queueMicrotask(() => {
                                    if (getFails) {
                                        anfrage.onerror && anfrage.onerror();
                                    } else {
                                        anfrage.result = vorhandeneZeile;
                                        anfrage.onsuccess && anfrage.onsuccess();
                                    }
                                });
                                return anfrage;
                            },
                            put() {
                                queueMicrotask(() => tx.oncomplete && tx.oncomplete());
                            },
                        });
                        return tx;
                    },
                };
                req.onsuccess && req.onsuccess();
            });
            return req;
        },
    };
}

/** Rohzugriff auf eine Zeile der Krypto-IndexedDB (es gibt keine oeffentliche
 *  Leseschnittstelle fuer den Aktivitaetsstempel). */
function rohZeile(key) {
    return new Promise((resolve, reject) => {
        const req = ECHTES_IDB.open("anlaufstelle-crypto", 1);
        req.onerror = () => reject(req.error);
        req.onsuccess = () => {
            const db = req.result;
            const tx = db.transaction("meta", "readonly");
            const get = tx.objectStore("meta").get(key);
            get.onsuccess = () => {
                db.close();
                resolve(get.result || null);
            };
            get.onerror = () => {
                db.close();
                reject(get.error);
            };
        };
    });
}

function rohSchreiben(record) {
    return new Promise((resolve, reject) => {
        const req = ECHTES_IDB.open("anlaufstelle-crypto", 1);
        req.onerror = () => reject(req.error);
        req.onsuccess = () => {
            const db = req.result;
            const tx = db.transaction("meta", "readwrite");
            tx.objectStore("meta").put(record);
            tx.oncomplete = () => {
                db.close();
                resolve();
            };
            tx.onerror = () => {
                db.close();
                reject(tx.error);
            };
        };
    });
}

async function schluesselAblegen() {
    await cryptoSession.deriveSessionKey("richtiges pferd batterie", SALT);
    expect(cryptoSession.hasSessionKey()).toBe(true);
}

describe("clearSessionKey meldet den Ausgang der persistenten Loeschung", () => {
    it("liefert {persisted:true}, wenn die Zeile wirklich weg ist", async () => {
        await schluesselAblegen();

        const ergebnis = await cryptoSession.clearSessionKey();

        expect(ergebnis).toEqual({ persisted: true });
        expect(cryptoSession.hasSessionKey()).toBe(false);
        await expect(cryptoSession.hasPersistedSessionKey()).resolves.toBe(false);
    });

    it("liefert {persisted:false}, wenn der Delete scheitert — und wirft NICHT", async () => {
        // Das ist der Kern des Befunds: vorher war dieser Fall von einem
        // Erfolg nicht zu unterscheiden.
        await schluesselAblegen();
        _setIndexedDb(kaputtesIndexedDb({ deleteFails: true }));

        const ergebnis = await cryptoSession.clearSessionKey();

        expect(ergebnis).toEqual({ persisted: false });
    });

    it("verwirft den Memory-Schluessel auch dann, wenn der Delete scheitert", async () => {
        // Der lokale Riegel darf nicht am IndexedDB-Fehler haengen: ohne
        // Memory-Key ist in DIESEM Tab nichts mehr entschluesselbar.
        await schluesselAblegen();
        _setIndexedDb(kaputtesIndexedDb({ deleteFails: true }));

        await cryptoSession.clearSessionKey();

        expect(cryptoSession.hasSessionKey()).toBe(false);
    });

    it("bricht bestehende Aufrufer nicht — kein throw, awaitbar wie bisher", async () => {
        // wipeOfflineState, der Idle-Wipe, der Passwortwechsel in
        // auth-bootstrap.js und ~20 E2E-Stellen ``await``en diese Funktion und
        // ignorieren ihren Rueckgabewert. Eine Rejection haette sie alle
        // gebrochen — deshalb der Rueckgabewert statt einer Ausnahme.
        await schluesselAblegen();
        _setIndexedDb(kaputtesIndexedDb({ deleteFails: true }));

        await expect(
            Promise.all([cryptoSession.clearSessionKey(), Promise.resolve("anderer Zweig")])
        ).resolves.toEqual([{ persisted: false }, "anderer Zweig"]);
    });
});

describe("Aktivitaetsstempel und Idle-Wipe (R-L3-14)", () => {
    // Warum das zaehlt: ``_enforceIdleWipeLocked`` startet ein FRISCHES
    // Zeitfenster, wenn es keinen Stempel findet — statt den ueberfaelligen
    // Wipe erneut zu versuchen. Wuerde der Stempel nach einem GESCHEITERTEN
    // Key-Delete mitgeloescht, verschoebe sich die naechste Loeschprobe um eine
    // volle Sitzungsdauer, obwohl der Schluessel noch auf dem Geraet liegt.

    it("laesst den Stempel stehen, wenn der Key-Delete scheitert", async () => {
        await schluesselAblegen();
        await rohSchreiben({ key: "lastActivity", ts: 1 });
        const protokoll = [];
        _setIndexedDb(kaputtesIndexedDb({ deleteFailsFor: ["sessionKey"], protokoll }));

        const ergebnis = await cryptoSession.clearSessionKey();

        expect(ergebnis).toEqual({ persisted: false });
        // Der Stempel wurde nicht einmal ANGEFASST — obwohl sein Delete hier
        // gelungen waere.
        expect(protokoll).toEqual(["sessionKey"]);
        _setIndexedDb(ECHTES_IDB);
        expect(await rohZeile("lastActivity")).toEqual({ key: "lastActivity", ts: 1 });
    });

    it("loescht bei Erfolg beides — Schluessel und Stempel", async () => {
        await schluesselAblegen();
        await rohSchreiben({ key: "lastActivity", ts: 1 });

        const ergebnis = await cryptoSession.clearSessionKey();

        expect(ergebnis).toEqual({ persisted: true });
        expect(await rohZeile("sessionKey")).toBeNull();
        expect(await rohZeile("lastActivity")).toBeNull();
    });

    it("meldet trotzdem Erfolg, wenn nur der Stempel-Delete scheitert", async () => {
        // Der Stempel ist kein Geheimnis und ohne Schluessel wirkungslos — sein
        // Fehlschlag darf den Loeschnachweis nicht entwerten.
        await schluesselAblegen();
        const protokoll = [];
        _setIndexedDb(kaputtesIndexedDb({ deleteFailsFor: ["lastActivity"], protokoll }));

        const ergebnis = await cryptoSession.clearSessionKey();

        expect(ergebnis).toEqual({ persisted: true });
        expect(protokoll).toEqual(["sessionKey", "lastActivity"]);
    });
});

describe("hasPersistedSessionKey — die Rueckleseprobe", () => {
    it("meldet false, solange keine Zeile da ist", async () => {
        await schluesselAblegen();
        await cryptoSession.clearSessionKey();

        await expect(cryptoSession.hasPersistedSessionKey()).resolves.toBe(false);
    });

    it("meldet true, solange die Zeile steht", async () => {
        await schluesselAblegen();

        await expect(cryptoSession.hasPersistedSessionKey()).resolves.toBe(true);
    });

    it("meldet im Lesefehler konservativ true statt eine Zusage zu erfinden", async () => {
        _setIndexedDb(kaputtesIndexedDb({ getFails: true }));

        await expect(cryptoSession.hasPersistedSessionKey()).resolves.toBe(true);
    });
});

describe("Quick-Exit gegen das ECHTE crypto.js", () => {
    let quickExitGeladen = false;
    let broadcast;
    let replace;
    let warn;

    async function seiteAufbauen() {
        document.body.innerHTML = `
            <div id="quick-exit-bar"><button type="button" id="quick-exit-button">Schnell verlassen</button></div>
            <main id="main-content"></main>
            <div id="privacy-screen" hidden><div id="privacy-screen-panel">
                <button type="button" id="privacy-screen-resume">Fortfahren</button>
            </div></div>
        `;
        Object.keys(document.body.dataset).forEach((key) => delete document.body.dataset[key]);
        replace = vi.fn();
        Object.defineProperty(window, "location", {
            configurable: true,
            writable: true,
            value: { href: "http://localhost/clients/", pathname: "/clients/", replace, reload: vi.fn() },
        });
        broadcast = vi.fn();
        window.syncOrchestrator = { broadcast };
        warn = vi.spyOn(console, "warn").mockImplementation(() => {});
        if (!quickExitGeladen) {
            await import("../../src/static/js/quick-exit.js");
            quickExitGeladen = true;
        }
    }

    it("broadcastet nach einer echten, gelungenen Loeschung", async () => {
        await schluesselAblegen();
        await seiteAufbauen();

        await window.quickExit.trigger();

        await expect(cryptoSession.hasPersistedSessionKey()).resolves.toBe(false);
        expect(broadcast).toHaveBeenCalledWith({ type: "key-cleared" });
        expect(warn).not.toHaveBeenCalled();
        expect(replace).toHaveBeenCalledWith(window.quickExit.DEFAULT_URL);
    });

    it("broadcastet NICHT, wenn der echte Delete scheitert", async () => {
        // Genau der Weg aus dem Re-Review: ohne den crypto.js-Fix haette
        // clearSessionKey hier erfolgreich aufgeloest und der Broadcast waere
        // rausgegangen, obwohl die Zeile noch steht.
        await schluesselAblegen();
        await seiteAufbauen();
        _setIndexedDb(kaputtesIndexedDb({ deleteFails: true, vorhandeneZeile: { key: "sessionKey", cryptoKey: {} } }));

        await window.quickExit.trigger();

        expect(broadcast).not.toHaveBeenCalled();
        expect(warn).toHaveBeenCalledTimes(1);
        // Der Schutz vor Haengern bleibt: verlassen wird trotzdem.
        expect(replace).toHaveBeenCalledWith(window.quickExit.DEFAULT_URL);
    });
});
