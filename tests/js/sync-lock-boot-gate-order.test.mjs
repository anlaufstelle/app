// Refs #1493: a holder of the origin-wide offline lock must never wait for
// something that itself needs the lock.
//
// crypto.js runs its boot idle gate (``initialLoad`` -> ``_loadKey`` ->
// ``_runIdleGate``) under the exclusive Web Lock. If a sync sequence of the
// SAME tab asked for the lock first, the sequence holds it and then calls
// ``crypto_session.awaitReady()`` (replayQueue, revalidation, offline edits).
// ``awaitReady`` used to wait for ``initialLoad`` — which waits for the boot
// gate's lock request queued behind the sequence: a cycle. The lock stayed
// held for the whole origin and ``offline-sync-finished`` never fired.
//
// These tests load the REAL crypto.js and sync-orchestrator.js against a
// deterministic FIFO Web Lock double whose first grant is held back until both
// requests are queued, so the order "sequence before boot gate" is forced.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IDBFactory, IDBObjectStore } from "fake-indexeddb";

const DB_NAME = "anlaufstelle-crypto";
const META_TABLE = "meta";
const KEY_NAME = "sessionKey";
const ACTIVITY_KEY = "lastActivity";
const REVOCATION_KEY = "keyRevocation";
const LOCK_NAME = "anlaufstelle-offline-mutex";
const DEADLOCK_TIMEOUT_MS = 1000;

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

// FIFO double of ``navigator.locks`` with one exclusive lock. Grants are held
// back while ``paused``; ``resume()`` starts granting in request order.
function makeFifoLocks() {
    const queue = [];
    const log = [];
    let held = null;
    let paused = true;
    let nextId = 1;

    function pump() {
        if (paused || held || queue.length === 0) return;
        const entry = queue.shift();
        held = entry;
        log.push(`grant:${entry.id}`);
        Promise.resolve()
            .then(() => entry.callback({ name: entry.name, mode: "exclusive" }))
            .then(entry.resolve, entry.reject)
            .finally(() => {
                log.push(`release:${entry.id}`);
                held = null;
                pump();
            });
    }

    return {
        log,
        request(name, options, callback) {
            const cb = typeof options === "function" ? options : callback;
            return new Promise((resolve, reject) => {
                const entry = { id: nextId++, name, callback: cb, resolve, reject };
                queue.push(entry);
                log.push(`request:${entry.id}`);
                pump();
            });
        },
        pendingCount() {
            return queue.length;
        },
        isHeld() {
            return held !== null;
        },
        resume() {
            paused = false;
            pump();
        },
    };
}

let locks;

async function seedValidKey() {
    const now = Date.now();
    await _put({
        key: KEY_NAME,
        cryptoKey: { marker: "seeded" },
        createdAt: now,
        checkedAt: now,
        expiresAt: now + 3600_000,
    });
    await _put({ key: ACTIVITY_KEY, ts: now });
}

async function loadSyncModule() {
    await import("../../src/static/js/sync-orchestrator.js");
}

// Loads crypto.js and waits until its boot idle gate has queued its lock
// request behind the already queued request of the test.
async function loadCryptoBehindQueuedRequest() {
    await import("../../src/static/js/crypto.js");
    await vi.waitFor(() => expect(locks.pendingCount()).toBe(2));
}

// Ages the activity stamp far beyond any idle window.
function expireIdleWindow() {
    return _put({ key: ACTIVITY_KEY, ts: Date.now() - 365 * 24 * 3600_000 });
}

// Makes every read of the local revocation marker fail.
function failRevocationMarkerReads() {
    const original = IDBObjectStore.prototype.get;
    vi.spyOn(IDBObjectStore.prototype, "get").mockImplementation(function (key) {
        if (key === REVOCATION_KEY) throw new Error("marker read failed");
        return original.call(this, key);
    });
}

function withTimeout(promise) {
    return Promise.race([
        promise,
        new Promise((resolve) => setTimeout(() => resolve("timed-out"), DEADLOCK_TIMEOUT_MS)),
    ]);
}

beforeEach(async () => {
    vi.resetModules();
    const factory = new IDBFactory();
    globalThis.indexedDB = factory;
    window.indexedDB = factory;
    locks = makeFifoLocks();
    Object.defineProperty(navigator, "locks", { configurable: true, value: locks });
    window.offlineStore = {
        applyClientRevocations: vi.fn(async () => ({ status: "ok" })),
    };
    await seedValidKey();
});

afterEach(() => {
    delete navigator.locks;
    delete window.syncOrchestrator;
    delete window.syncOrchestratorPending;
    delete window.crypto_session;
    delete window.offlineQueue;
    delete window.offlineStore;
    vi.restoreAllMocks();
});

describe("Lock order: sync sequence queued before the boot idle gate (Refs #1493)", () => {
    it("finishes the sync sequence, fires offline-sync-finished and frees the lock", async () => {
        let keyDuringReplay = null;
        window.offlineQueue = {
            replayQueue: vi.fn(async () => {
                // Same entry gate as offline-queue.js replayQueue.
                await window.crypto_session?.awaitReady?.();
                keyDuringReplay = window.crypto_session.hasSessionKey();
            }),
        };
        await loadSyncModule();
        const finished = new Promise((resolve) =>
            window.addEventListener("offline-sync-finished", (ev) => resolve(ev.detail), { once: true })
        );

        // The reconnect queues the sync sequence first (request 1) ...
        window.dispatchEvent(new Event("online"));
        // ... then the boot idle gate of crypto.js queues behind it (request 2).
        await loadCryptoBehindQueuedRequest();
        locks.resume();

        expect(await withTimeout(finished)).not.toBe("timed-out");
        expect(window.offlineQueue.replayQueue).toHaveBeenCalledTimes(1);
        expect(keyDuringReplay).toBe(true);

        // The boot gate still runs UNDER the lock, strictly after the sequence.
        expect(await withTimeout(window.crypto_session.ready())).not.toBe("timed-out");
        await vi.waitFor(() => expect(locks.isHeld()).toBe(false));
        expect(locks.pendingCount()).toBe(0);
        expect(locks.log).toEqual([
            "request:1",
            "request:2",
            "grant:1",
            "release:1",
            "grant:2",
            "release:2",
        ]);
    });

    it.each(["awaitReady", "ready"])(
        "runExclusive user calling crypto_session.%s under the lock completes",
        async (gate) => {
            await loadSyncModule();
            // Another runExclusive user (offline edit, conflict resolver,
            // client view) holds the lock and needs the crypto session.
            const holder = window.syncOrchestrator.runExclusive(async () => {
                await window.crypto_session[gate]();
                return window.crypto_session.hasSessionKey();
            });
            await loadCryptoBehindQueuedRequest();
            locks.resume();

            expect(await withTimeout(holder)).toBe(true);
            expect(await withTimeout(window.crypto_session.ready())).not.toBe("timed-out");
            await vi.waitFor(() => expect(locks.isHeld()).toBe(false));
            expect(locks.log.slice(-2)).toEqual(["grant:2", "release:2"]);
        }
    );

    it("awaitReady under the lock resolves only after the idle check when the marker read fails", async () => {
        window.offlineStore.hasUnsyncedData = vi.fn(async () => true);
        await loadSyncModule();
        const holder = window.syncOrchestrator.runExclusive(async () => {
            await window.crypto_session.awaitReady();
            const usableBefore = window.crypto_session.hasSessionKey();
            await expireIdleWindow();
            failRevocationMarkerReads();
            await window.crypto_session.awaitReady();
            return { usableBefore, usableAfter: window.crypto_session.hasSessionKey() };
        });
        await loadCryptoBehindQueuedRequest();
        locks.resume();

        expect(await withTimeout(holder)).toEqual({ usableBefore: true, usableAfter: false });
        expect(window.offlineStore.hasUnsyncedData).toHaveBeenCalled();
    });

    it("boot ready() settles only after the idle check when the marker read fails", async () => {
        window.offlineStore.hasUnsyncedData = vi.fn(async () => true);
        await expireIdleWindow();
        failRevocationMarkerReads();
        await loadSyncModule();
        locks.resume();
        await import("../../src/static/js/crypto.js");

        expect(await withTimeout(window.crypto_session.ready())).not.toBe("timed-out");
        expect(window.offlineStore.hasUnsyncedData).toHaveBeenCalled();
        expect(window.crypto_session.hasSessionKey()).toBe(false);
    });

    it("an independent ready() during an early-releasing holder never wipes under a foreign lock", async () => {
        let foreignHeld = false;
        const purgesUnderForeignLock = [];
        let stockReads = 0;
        window.offlineStore.purgeAll = vi.fn(async () => {
            purgesUnderForeignLock.push(foreignHeld);
        });
        // First stock read (the direct gate) answers "nothing unsynced" once a
        // foreign tab holds the lock, or after a fallback; later reads (the
        // queued boot gate) report unsynced work, so they only lock the key.
        window.offlineStore.hasUnsyncedData = vi.fn(() => {
            stockReads += 1;
            if (stockReads > 1) return Promise.resolve(true);
            return new Promise((resolve) => {
                const started = Date.now();
                const poll = () =>
                    foreignHeld || Date.now() - started > 200 ? resolve(false) : setTimeout(poll, 1);
                poll();
            });
        });
        await expireIdleWindow();
        await loadSyncModule();

        let independent = null;
        // Own holder: starts an unrelated ready() chain and releases at once.
        const holder = window.syncOrchestrator.runExclusive(async () => {
            independent = window.crypto_session.ready();
            return "released";
        });
        await loadCryptoBehindQueuedRequest();
        // Another tab asks for the lock after the boot gate.
        const foreign = locks.request(LOCK_NAME, { mode: "exclusive" }, async () => {
            foreignHeld = true;
            await new Promise((resolve) => setTimeout(resolve, 150));
            foreignHeld = false;
        });
        locks.resume();

        expect(await withTimeout(holder)).toBe("released");
        expect(await withTimeout(foreign)).not.toBe("timed-out");
        expect(await withTimeout(independent)).not.toBe("timed-out");
        await vi.waitFor(() => expect(locks.isHeld()).toBe(false));
        expect(purgesUnderForeignLock).not.toContain(true);
    });

    it("boot hydration waits for a sync module loaded late in the page parse", async () => {
        // The parser may block on scripts between crypto.js and
        // sync-orchestrator.js, so IndexedDB callbacks run before the sync
        // module exists. Simulated deterministically: the sync module is
        // loaded, hidden, and shows up during the second activity read.
        let readyState = "loading";
        vi.spyOn(document, "readyState", "get").mockImplementation(() => readyState);
        await loadSyncModule();
        const syncModule = window.syncOrchestrator;
        delete window.syncOrchestrator;
        const original = IDBObjectStore.prototype.get;
        let activityReads = 0;
        vi.spyOn(IDBObjectStore.prototype, "get").mockImplementation(function (key) {
            if (key === ACTIVITY_KEY && ++activityReads >= 2) window.syncOrchestrator = syncModule;
            return original.call(this, key);
        });
        locks.resume();
        await import("../../src/static/js/crypto.js");
        await new Promise((resolve) => setTimeout(resolve, 50));
        window.syncOrchestrator = syncModule;
        readyState = "interactive";
        document.dispatchEvent(new Event("DOMContentLoaded"));

        expect(await withTimeout(window.crypto_session.ready())).not.toBe("timed-out");
        const holder = window.syncOrchestrator.runExclusive(async () => {
            await window.crypto_session.awaitReady();
            return window.crypto_session.hasSessionKey();
        });
        expect(await withTimeout(holder)).toBe(true);
        await vi.waitFor(() => expect(locks.isHeld()).toBe(false));
    });
});
