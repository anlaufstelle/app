// Idle wipe decided once per tab while the offline lock is held.
// A gate reached through the direct (lock-held) path during a running idle
// wipe must not revoke the key a second time: that late revocation would
// reject a re-login derivation started right after the wipe finished. Each
// gate still checks the absolute key lifetime on its own.
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import { createContext, runInContext } from "node:vm";
import { IDBFactory } from "fake-indexeddb";
import { describe, expect, it, vi } from "vitest";

const cryptoSource = readFileSync("src/static/js/crypto.js", "utf8");
const PASSWORD = "test password";
const SALT = "c2FsdHNhbHRzYWx0";
const START = Date.parse("2026-10-21T08:00:00Z");

function deferred() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
}

// Let fake-indexeddb run every queued request (it schedules on macrotasks).
async function drainIndexedDb() {
    for (let i = 0; i < 50; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 0));
    }
}

function newTab() {
    const factory = new IDBFactory();
    const clock = { now: START };
    class ClockDate extends Date {
        static now() { return clock.now; }
    }
    // Optionally hold the success of one later ``open`` call, i.e. one
    // IndexedDB read of the module, until the test releases it.
    let heldOpen = null;
    const indexedDB = {
        open(name, version) {
            const request = factory.open(name, version);
            const hold = heldOpen;
            if (!hold || hold.skip-- > 0) return request;
            heldOpen = null;
            const held = {
                onsuccess: null, onerror: null, onupgradeneeded: null,
                get result() { return request.result; },
                get error() { return request.error; },
            };
            request.onupgradeneeded = (event) => held.onupgradeneeded?.(event);
            request.onerror = (event) => held.onerror?.(event);
            request.onsuccess = (event) => {
                hold.release.promise.then(() => held.onsuccess?.(event));
            };
            hold.reached.resolve();
            return held;
        },
    };
    function holdOpen(skip) {
        const hold = { skip, reached: deferred(), release: deferred() };
        heldOpen = hold;
        return { reached: hold.reached.promise, release: () => hold.release.resolve() };
    }
    let depth = 0;
    let tail = Promise.resolve();
    const syncOrchestrator = {
        isLockHeld: () => depth > 0,
        runExclusive(fn) {
            const run = tail.then(async () => {
                depth += 1;
                try {
                    return await fn();
                } finally {
                    depth -= 1;
                }
            });
            tail = run.catch(() => {});
            return run;
        },
        broadcast() {},
    };
    const unsyncedChecks = [];
    const offlineStore = {
        hasUnsyncedData() {
            const check = deferred();
            unsyncedChecks.push(check);
            return check.promise;
        },
    };
    const window = {
        crypto: {
            getRandomValues: webcrypto.getRandomValues.bind(webcrypto),
            subtle: {
                importKey: webcrypto.subtle.importKey.bind(webcrypto.subtle),
                deriveKey: webcrypto.subtle.deriveKey.bind(webcrypto.subtle),
                encrypt: webcrypto.subtle.encrypt.bind(webcrypto.subtle),
            },
        },
        indexedDB, addEventListener() {}, setInterval() {}, syncOrchestrator, offlineStore,
    };
    const document = {
        body: { dataset: { offlineKeyMaxAge: "3600", sessionAge: "7200" } },
        querySelectorAll: () => [], addEventListener() {}, readyState: "complete",
    };
    runInContext(cryptoSource, createContext({
        window, document, indexedDB, TextEncoder, TextDecoder, atob, btoa, console, Date: ClockDate,
    }));
    return { indexedDB: factory, clock, holdOpen, session: window.crypto_session, unsyncedChecks };
}

function expireActivity(indexedDB) {
    return new Promise((resolve, reject) => {
        const open = indexedDB.open("anlaufstelle-crypto", 1);
        open.onsuccess = () => {
            const tx = open.result.transaction("meta", "readwrite");
            tx.objectStore("meta").put({ key: "lastActivity", ts: 1 });
            tx.oncomplete = () => { open.result.close(); resolve(); };
            tx.onerror = () => reject(tx.error);
        };
        open.onerror = () => reject(open.error);
    });
}

describe("idle wipe with unsynced work", () => {
    it("keeps a re-login started after the wipe valid despite a concurrent lock-held gate", async () => {
        const tab = newTab();
        await tab.session.ready();
        await tab.session.deriveSessionKey(PASSWORD, SALT);
        await expireActivity(tab.indexedDB);

        const wipe = tab.session.enforceIdleWipe();
        await vi.waitFor(() => expect(tab.unsyncedChecks).toHaveLength(1));
        // A boot chain of this tab reaches the idle gate while the wipe holds
        // the lock, i.e. through the direct path.
        const concurrentGate = tab.session.awaitReady();
        await drainIndexedDb();

        tab.unsyncedChecks[0].resolve(true);
        expect(await wipe).toBe(true);
        expect(tab.session.hasSessionKey()).toBe(false);

        const relogin = tab.session.deriveSessionKey(PASSWORD, SALT).then(
            () => "derived",
            (error) => error.name,
        );
        for (const check of tab.unsyncedChecks.slice(1)) check.resolve(true);
        await concurrentGate;

        expect(await relogin).toBe("derived");
        await tab.session.awaitReady();
        expect(tab.session.hasSessionKey()).toBe(true);
    });
});

describe("absolute key lifetime during a running idle decision", () => {
    it("denies the key to a gate that enters after the deadline", async () => {
        const tab = newTab();
        // The store receives the primitives once, as offline-db.js does.
        const { encryptPayload } = tab.session._link();
        await tab.session.ready();
        await tab.session.deriveSessionKey(PASSWORD, SALT);
        tab.clock.now = START + 3_599_000;

        // The gate opens the database for its lifetime check first and for
        // its activity read second; hold the activity read.
        const hold = tab.holdOpen(1);
        const decision = tab.session.enforceIdleWipe();
        await hold.reached;
        tab.clock.now = START + 3_600_000;
        // A chain of this tab needs the key after the deadline while the
        // decision still holds the lock, i.e. through the direct path.
        const late = encryptPayload({ note: "after deadline" }).then(
            () => "encrypted",
            (error) => error.name,
        );
        await drainIndexedDb();
        hold.release();

        expect(await late).toBe("NoSessionKeyError");
        await decision;
        expect(tab.session.hasSessionKey()).toBe(false);
        expect(await tab.session.hasPersistedSessionKey()).toBe(false);
    });
});
