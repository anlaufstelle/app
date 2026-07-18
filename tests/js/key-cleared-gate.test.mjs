// Refs #1393 (Boot-Gate): Der key-cleared-Empfaenger in crypto.js darf eine
// laufende Fremd-Tab-Sync-Sequenz NICHT transient abbrechen. Zwei Haertungen
// werden hier geprueft:
//
//   1. Geteilter Key-Zustand: key-cleared wird nur konsumiert, wenn die
//      IndexedDB-Grundlage wirklich (noch) fehlt. Hat sich zwischenzeitlich
//      ein Fremd-Tab neu angemeldet (KEY_NAME wieder da), bleibt der
//      in-memory-Key erhalten (re-verankert) statt verworfen zu werden.
//   2. Web-Lock-Halter nicht von aussen abbrechen: haelt DIESER Tab gerade
//      den Sync-Lock (mitten in einer Sequenz), wird der Drop DAHINTER
//      gequeued und passiert erst ZWISCHEN Sequenzen — die laufende Sequenz
//      sieht den Key bis zu ihrem Ende.
//
// crypto.js ist eine IIFE, die sich beim Laden an ``window`` haengt und den
// Empfaenger sofort via ``window.syncOrchestrator.onMessage`` registriert
// (window.syncOrchestrator existiert im Test VOR dem Import). Wir treiben den
// Empfaenger direkt ueber den erfassten Callback. Der Memory-Key wird beim
// eager ``_loadKey()`` aus einem vorab geseedeten IndexedDB-Row hydriert
// (KEIN WebCrypto noetig — cachedKey haelt schlicht das gespeicherte Objekt).
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { IDBFactory } from "fake-indexeddb";

const DB_NAME = "anlaufstelle-crypto";
const META_TABLE = "meta";
const KEY_NAME = "sessionKey";

// Oeffnet (und erstellt) den crypto-Store in DERSELBEN IndexedDB, die crypto.js
// nutzt — Version 1, damit crypto.js' eigenes open(v1) kein Upgrade mehr
// ausloest und den Store vorfindet.
function _openSeedDb() {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(DB_NAME, 1);
        req.onupgradeneeded = () => {
            const db = req.result;
            if (!db.objectStoreNames.contains(META_TABLE)) {
                db.createObjectStore(META_TABLE, { keyPath: "key" });
            }
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
}

function _put(record) {
    return _openSeedDb().then(
        (db) =>
            new Promise((resolve, reject) => {
                const tx = db.transaction(META_TABLE, "readwrite");
                tx.objectStore(META_TABLE).put(record);
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

function _delete(key) {
    return _openSeedDb().then(
        (db) =>
            new Promise((resolve, reject) => {
                const tx = db.transaction(META_TABLE, "readwrite");
                tx.objectStore(META_TABLE).delete(key);
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

// Minimaler Fake-Orchestrator mit EINEM exklusiven, origin-weiten Lock (eine
// serielle Kette), wie navigator.locks ihn dem Sync-Orchestrator liefert.
// ``runExclusive`` reiht Anforderungen hinter dem aktuell Haltenden ein;
// ``isLockHeld`` spiegelt, ob gerade eine fn unter dem Lock laeuft.
function makeOrchestrator() {
    let chain = Promise.resolve();
    let depth = 0;
    const listeners = [];
    return {
        runExclusive(fn) {
            const run = chain.then(async () => {
                depth += 1;
                try {
                    return await fn();
                } finally {
                    depth -= 1;
                }
            });
            chain = run.catch(() => {});
            return run;
        },
        isLockHeld() {
            return depth > 0;
        },
        broadcast() {},
        onMessage(cb) {
            if (typeof cb === "function") listeners.push(cb);
        },
        // Testtreiber: liefert eine BroadcastChannel-Nachricht an die
        // registrierten Empfaenger (BroadcastChannel liefert nicht an den
        // Absender selbst — hier simulieren wir den EMPFANGENDEN Tab).
        deliver(msg) {
            listeners.slice().forEach((l) => l(msg));
        },
    };
}

let orch;

async function loadCryptoWithKey() {
    // Memory-Key hydrieren: KEY_NAME vorab seeden, dann crypto.js importieren
    // (eager _loadKey laedt das Row in cachedKey; ohne lastActivity-Stamp
    // startet der Idle-Gate ein frisches Fenster statt zu wipen).
    await _put({ key: KEY_NAME, cryptoKey: { marker: "grounded" } });
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.ready();
}

beforeEach(() => {
    vi.resetModules();
    // Frische IndexedDB pro Test (der IIFE-Modulzustand ist sonst geteilt).
    const factory = new IDBFactory();
    globalThis.indexedDB = factory;
    window.indexedDB = factory;
    orch = makeOrchestrator();
    window.syncOrchestrator = orch;
});

afterEach(() => {
    delete window.syncOrchestrator;
    delete window.crypto_session;
    delete window.offlineEdit;
    vi.restoreAllMocks();
});

describe("key-cleared Boot-Gate (Refs #1393)", () => {
    it("verwirft den Memory-Key, wenn die IndexedDB-Grundlage nachweislich fehlt", async () => {
        await loadCryptoWithKey();
        expect(window.crypto_session.hasSessionKey()).toBe(true);

        const refreshCounts = vi.fn();
        window.offlineEdit = { refreshCounts };

        // Der wipende Fremd-Tab hat KEY_NAME bereits geloescht.
        await _delete(KEY_NAME);

        orch.deliver({ type: "key-cleared" });
        // Den unter dem Lock gequeuten Apply abwarten.
        await orch.runExclusive(() => {});

        expect(window.crypto_session.hasSessionKey()).toBe(false);
        expect(refreshCounts).toHaveBeenCalledTimes(1);
    });

    it("behaelt den Memory-Key, wenn die IndexedDB-Grundlage weiter besteht (Fremd-Tab-Re-Login)", async () => {
        await loadCryptoWithKey();
        expect(window.crypto_session.hasSessionKey()).toBe(true);

        const refreshCounts = vi.fn();
        window.offlineEdit = { refreshCounts };

        // KEY_NAME liegt weiter in der IndexedDB (ein Fremd-Tab hat neu
        // abgeleitet) -> der Boot-Wipe-Broadcast darf ihn nicht wegraeumen.
        orch.deliver({ type: "key-cleared" });
        await orch.runExclusive(() => {});

        expect(window.crypto_session.hasSessionKey()).toBe(true);
        expect(refreshCounts).not.toHaveBeenCalled();
    });

    it("bricht einen laufenden Sequenz-Halter dieses Tabs NICHT ab (Drop erst zwischen Sequenzen)", async () => {
        await loadCryptoWithKey();
        // Grundlage fehlt (echter Fremd-Wipe) -> der Drop WUERDE fallen ...
        await _delete(KEY_NAME);

        // ... aber dieser Tab haelt gerade den Sync-Lock (Sequenz laeuft).
        let releaseSequence;
        const gate = new Promise((res) => {
            releaseSequence = res;
        });
        let keyDuringSequence = null;
        const sequence = orch.runExclusive(async () => {
            await gate;
            // Die laufende Sequenz sieht den Key bis zu ihrem Ende.
            keyDuringSequence = window.crypto_session.hasSessionKey();
        });

        // key-cleared trifft ein, WAEHREND die Sequenz laeuft.
        orch.deliver({ type: "key-cleared" });
        await Promise.resolve();
        await Promise.resolve();

        // Kein Abbruch von aussen: der Key steht noch.
        expect(window.crypto_session.hasSessionKey()).toBe(true);
        expect(orch.isLockHeld()).toBe(true);

        // Sequenz zu Ende laufen lassen -> danach greift der gequeute Drop.
        releaseSequence();
        await sequence;
        await orch.runExclusive(() => {});

        expect(keyDuringSequence).toBe(true);
        expect(window.crypto_session.hasSessionKey()).toBe(false);
    });

    it("ignoriert Nachrichten anderen Typs", async () => {
        await loadCryptoWithKey();
        await _delete(KEY_NAME);

        orch.deliver({ type: "sync-finished" });
        orch.deliver({});
        orch.deliver(null);
        await orch.runExclusive(() => {});

        expect(window.crypto_session.hasSessionKey()).toBe(true);
    });
});
