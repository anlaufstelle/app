// Idle wipe decided once per tab while the offline lock is held.
// A gate reached through the direct (lock-held) path during a running idle
// wipe must not revoke the key a second time: that late revocation would
// reject a re-login derivation started right after the wipe finished.
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import { createContext, runInContext } from "node:vm";
import { IDBFactory } from "fake-indexeddb";
import { describe, expect, it, vi } from "vitest";

const cryptoSource = readFileSync("src/static/js/crypto.js", "utf8");
const PASSWORD = "test password";
const SALT = "c2FsdHNhbHRzYWx0";

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
    const indexedDB = new IDBFactory();
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
            subtle: {
                importKey: webcrypto.subtle.importKey.bind(webcrypto.subtle),
                deriveKey: webcrypto.subtle.deriveKey.bind(webcrypto.subtle),
            },
        },
        indexedDB, addEventListener() {}, setInterval() {}, syncOrchestrator, offlineStore,
    };
    const document = {
        body: { dataset: { offlineKeyMaxAge: "3600", sessionAge: "7200" } },
        querySelectorAll: () => [], addEventListener() {}, readyState: "complete",
    };
    runInContext(cryptoSource, createContext({
        window, document, indexedDB, TextEncoder, TextDecoder, atob, btoa, console,
    }));
    return { indexedDB, session: window.crypto_session, unsyncedChecks };
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
