// A failed open of the local store at page load must not leave a half-initialised
// handle behind: the next access opens the store again, so the "try again" button of
// the tally notice (and the autosave retry) recover without a page reload.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Dexie from "dexie";

import { importOfflineStoreModules } from "./_offline_store_modules.mjs";

const TYPE_A = "11111111-1111-4111-8111-111111111111";
const noticeSelector = "[data-offline-tally-unknown]";

const nativeTransaction = IDBDatabase.prototype.transaction;
let failOpen = false;
// Number of open attempts that ran into the injected failure.
let failedOpenAttempts = 0;

// Dexie reads the stored schema through a short read transaction while opening an
// existing database. Failing exactly those calls reproduces a store that cannot be
// opened at page load, without breaking the fake IndexedDB implementation itself.
function installOpenFailure() {
    IDBDatabase.prototype.transaction = function (...args) {
        if (failOpen && /node_modules\/dexie/.test(new Error().stack || "")) {
            failedOpenAttempts += 1;
            throw new DOMException("injected open failure", "UnknownError");
        }
        return nativeTransaction.apply(this, args);
    };
}

async function loadModules() {
    vi.resetModules();
    delete window.offlineStore;
    delete window.offlineTallyUi;
    window.Dexie = Dexie;
    globalThis.Dexie = Dexie;
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.deriveSessionKey("correct horse battery", "c2FsdHNhbHRzYWx0");
    await importOfflineStoreModules();
}

async function bootWithFailingOpen() {
    // First load creates the database, so that the failing load opens an existing one.
    await loadModules();
    await window.offlineStore.getMetaValue("warm-up");
    window.offlineStore.db.close();
    installOpenFailure();
    failOpen = true;
    await loadModules();
    return window.offlineStore;
}

// The rejection of an access to a store that cannot open is a regular open failure,
// never the TypeError of a half-initialised handle.
async function rejection(promise) {
    let error = null;
    try {
        await promise;
    } catch (caught) {
        error = caught;
    }
    expect(error).not.toBeNull();
    expect(error.name).not.toBe("TypeError");
    return error;
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

beforeEach(() => {
    failedOpenAttempts = 0;
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
    failOpen = false;
    IDBDatabase.prototype.transaction = nativeTransaction;
    vi.restoreAllMocks();
});

describe("local store that failed to open at page load", () => {
    it("rejects the first access and opens the store again on the next one", async () => {
        const store = await bootWithFailingOpen();
        await settle();

        await rejection(store.getMetaValue("probe"));
        await settle();

        failOpen = false;
        await expect(store.getMetaValue("probe")).resolves.toBeNull();
        await store.setMetaValue("written", "value");
        await expect(store.getMetaValue("written")).resolves.toBe("value");
    });

    it("keeps rejecting while the failure persists and recovers afterwards", async () => {
        const store = await bootWithFailingOpen();
        await settle();

        for (let attempt = 0; attempt < 3; attempt += 1) {
            const before = failedOpenAttempts;
            await rejection(store.getMetaValue("probe"));
            // Every access ran a new open attempt that failed again.
            expect(failedOpenAttempts).toBe(before + 1);
            await settle();
        }

        failOpen = false;
        await expect(store.getMetaValue("probe")).resolves.toBeNull();
    });

    it("lets the autosave read and write paths work again", async () => {
        const store = await bootWithFailingOpen();
        await settle();

        await rejection(store.listDecrypted("meta", () => true));
        await settle();
        await rejection(store.putEncrypted("drafts", { formKey: "f", data: { a: 1 } }));
        await settle();

        failOpen = false;
        await expect(store.listDecrypted("meta", () => true)).resolves.toEqual([]);
        await store.putEncrypted("drafts", { formKey: "f", data: { a: 1 } });
        const draft = await store.getDecrypted("drafts", "f");
        expect(draft.data).toEqual({ a: 1 });
    });

    it("recovers the tally panel through the retry button of the notice", async () => {
        await bootWithFailingOpen();
        window.syncOrchestrator = { requestSync: vi.fn(async () => {}) };
        document.body.innerHTML = `<section id="tally-panel"><div data-testid="tally-row-${TYPE_A}"><span data-testid="tally-count-${TYPE_A}">12</span></div></section>`;
        await import("../../src/static/js/offline-tally.js");
        const ui = window.offlineTallyUi;
        await settle();

        await ui.refreshOnlinePanel();
        expect(document.querySelectorAll(noticeSelector)).toHaveLength(1);

        // A repeated failure keeps the notice, it is neither dropped nor stacked; the
        // click ran a new open attempt that failed again.
        const before = failedOpenAttempts;
        document.querySelector(`${noticeSelector} button`).click();
        await vi.waitFor(() => expect(failedOpenAttempts).toBeGreaterThan(before));
        await settle();
        expect(document.querySelectorAll(noticeSelector)).toHaveLength(1);

        failOpen = false;
        document.querySelector(`${noticeSelector} button`).click();
        await vi.waitFor(() => expect(document.querySelector(noticeSelector)).toBeNull());
        expect(document.querySelector(`[data-testid="tally-count-${TYPE_A}"]`).textContent).toBe("12");
    });
});
