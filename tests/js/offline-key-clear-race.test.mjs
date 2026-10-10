// Refs #2528.
// Force real IndexedDB interleavings through the public storage/logout APIs.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import Dexie from "dexie";
import { IDBObjectStore } from "fake-indexeddb";
import { importOfflineStoreModules } from "./_offline_store_modules.mjs";

const START = Date.parse("2026-10-10T08:00:00Z");
const PASSWORD = "correct horse battery";
const SALT = "c2FsdHNhbHRzYWx0";
let now = START;
let session;
let store;
let replace;
let keyClearedReceiver;

beforeAll(async () => {
    window.Dexie = globalThis.Dexie = Dexie;
    const timer = vi.spyOn(window, "setInterval").mockReturnValue(1);
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    session = window.crypto_session;
    await session.ready();
    keyClearedReceiver = window.syncOrchestratorPending.at(-1);
    timer.mockRestore();
    await importOfflineStoreModules();
    store = window.offlineStore;
    await import("../../src/static/js/sw-register.js");
    await import("../../src/static/js/quick-exit.js");
});

beforeEach(async () => {
    now = START;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    document.body.dataset.offlineKeyMaxAge = "3600";
    document.body.dataset.sessionAge = "7200";
    document.body.innerHTML = '<main id="main-content"></main><div id="privacy-screen" hidden></div>';
    replace = vi.fn();
    Object.defineProperty(window, "location", {
        configurable: true,
        value: { href: "http://localhost/clients/", pathname: "/clients/", replace },
    });
    await store.purgeAll();
    await session.clearSessionKey();
    await session.deriveSessionKey(PASSWORD, SALT);
});

afterEach(() => {
    delete window.syncOrchestrator;
    delete window.__logoutInFlight;
    delete window.__quickExitInFlight;
    vi.restoreAllMocks();
});

function readKeyRow() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open("anlaufstelle-crypto", 1);
        request.onerror = () => reject(request.error);
        request.onsuccess = () => {
            const db = request.result;
            const tx = db.transaction("meta", "readonly");
            const read = tx.objectStore("meta").get("sessionKey");
            tx.oncomplete = () => { db.close(); resolve(read.result); };
            tx.onerror = () => { db.close(); reject(tx.error); };
        };
    });
}

function onKeyRequest(method, stage, action) {
    const original = IDBObjectStore.prototype[method];
    let fired = false;
    vi.spyOn(IDBObjectStore.prototype, method).mockImplementation(function (value) {
        const request = original.apply(this, arguments);
        if (!fired && (method === "get" ? value === "sessionKey" : value.key === "sessionKey")) {
            fired = true;
            // Install before crypto.js attaches its own request/transaction handler.
            const target = stage === "read" ? request : this.transaction;
            target.addEventListener(stage === "read" ? "success" : "complete", action, { once: true });
        }
        return request;
    });
    return () => expect(fired).toBe(true);
}

async function assertCleared() {
    expect(await session.hasPersistedSessionKey()).toBe(false);
    expect(await readKeyRow()).toBeUndefined();
    expect(session.hasSessionKey()).toBe(false);
}

describe("key clearing during the lifetime gate", () => {
    it.each(["logout", "quick-exit"])("keeps the key cleared after %s overtakes the key read", async (path) => {
        // The gate already holds the lock; logout/quick-exit must not queue behind it.
        const lock = vi.fn(() => new Promise(() => {}));
        const broadcast = vi.fn();
        window.syncOrchestrator = { isLockHeld: () => true, runExclusive: lock, broadcast };
        const form = document.createElement("form");
        form.action = "/logout/";
        form.submit = vi.fn();
        document.body.appendChild(form);
        let exit;
        const assertFired = onKeyRequest("get", "read", () => {
            if (path === "logout") form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
            else exit = window.quickExit.trigger();
            expect(session.hasSessionKey()).toBe(false);
        });
        const write = store.putEncrypted("events", { pk: "race", data: { note: "private" } })
            .then(() => "encrypted", (error) => error.name);
        const outcome = await write;
        assertFired();
        if (path === "logout") await vi.waitFor(() => expect(form.submit).toHaveBeenCalledOnce());
        else {
            await exit;
            expect(replace).toHaveBeenCalledOnce();
            expect(broadcast).toHaveBeenCalledWith({ type: "key-cleared" });
        }
        await assertCleared();
        expect(outcome).toBe("NoSessionKeyError");
        expect(lock).not.toHaveBeenCalled();
        await session.awaitReady();
        await assertCleared();
    });

    it("does not rehydrate memory when clearing starts at high-water transaction completion", async () => {
        let clearing;
        const assertFired = onKeyRequest("put", "commit", () => { clearing = session.clearSessionKey(); });
        await session.awaitReady();
        assertFired();
        expect(session.hasSessionKey()).toBe(false);
        await clearing;
        await assertCleared();
    });

    it("cannot recreate a row deleted by another database connection after the read", async () => {
        const db = await new Promise((resolve) => {
            const request = indexedDB.open("anlaufstelle-crypto", 1);
            request.onsuccess = () => resolve(request.result);
        });
        let deleting;
        const assertFired = onKeyRequest("get", "read", () => {
            deleting = new Promise((resolve, reject) => {
                const tx = db.transaction("meta", "readwrite");
                tx.objectStore("meta").delete("sessionKey");
                tx.oncomplete = resolve;
                tx.onerror = () => reject(tx.error);
            });
        });
        try {
            await session.awaitReady();
            assertFired();
            await deleting;
            expect(await session.hasPersistedSessionKey()).toBe(false);
            await session.awaitReady();
            await assertCleared();
        } finally {
            db.close();
        }
    });

    it("does not rehydrate a broadcast receiver after a concurrent local clear", async () => {
        let receiving;
        let clearing;
        window.syncOrchestrator = {
            runExclusive: (fn) => { receiving = fn(); return receiving; },
        };
        const assertFired = onKeyRequest("get", "read", () => { clearing = session.clearSessionKey(); });
        keyClearedReceiver({ type: "key-cleared" });
        await receiving;
        assertFired();
        expect(session.hasSessionKey()).toBe(false);
        await clearing;
        await assertCleared();
    });

    it("continues persisting the high-water mark without clearing", async () => {
        const before = await readKeyRow();
        now += 600_000;
        await session.awaitReady();
        const after = await readKeyRow();
        expect(after.checkedAt).toBe(now);
        expect(after.createdAt).toBe(before.createdAt);
        expect(after.expiresAt).toBe(before.expiresAt);
        expect(session.hasSessionKey()).toBe(true);
    });

    it("continues deleting an expired key", async () => {
        now += 3600_000;
        await session.enforceIdleWipe();
        await assertCleared();
    });
});

describe("key clearing during login/demo derivation", () => {
    it("discards a derivation still computing when the key is cleared", async () => {
        let resume;
        const original = window.crypto.subtle.deriveKey.bind(window.crypto.subtle);
        vi.spyOn(window.crypto.subtle, "deriveKey").mockImplementation(async (...args) => {
            await new Promise((resolve) => { resume = resolve; });
            return original(...args);
        });
        const deriving = session.deriveSessionKey(PASSWORD, SALT)
            .then(() => "derived", (error) => error.name);
        await vi.waitFor(() => expect(resume).toBeTypeOf("function"));
        await session.clearSessionKey();
        await assertCleared();
        resume();
        const outcome = await deriving;
        await assertCleared();
        expect(outcome).toBe("NoSessionKeyError");
    });

    it("does not restore memory when clearing starts at derivation commit", async () => {
        let clearing;
        const assertFired = onKeyRequest("put", "commit", () => { clearing = session.clearSessionKey(); });
        const outcome = await session.deriveSessionKey(PASSWORD, SALT)
            .then(() => "derived", (error) => error.name);
        assertFired();
        expect(session.hasSessionKey()).toBe(false);
        await clearing;
        await assertCleared();
        expect(outcome).toBe("NoSessionKeyError");
    });
});
