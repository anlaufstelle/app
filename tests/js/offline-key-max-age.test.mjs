// Refs #2528: exercise encrypted storage and the public key lifecycle.
import { importOfflineStoreModules } from "./_offline_store_modules.mjs";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import Dexie from "dexie";

const START = Date.parse("2026-10-09T08:00:00Z");
const PASSWORD = "correct horse battery";
const SALT = "c2FsdHNhbHRzYWx0";
const PK = "33333333-3333-4333-8333-333333333333";
let now = START;
let store;
let checkInterval;

beforeAll(async () => {
    window.Dexie = globalThis.Dexie = Dexie;
    const timer = vi.spyOn(window, "setInterval").mockImplementation((fn) => {
        checkInterval = fn;
        return 1;
    });
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.ready();
    timer.mockRestore();
    await importOfflineStoreModules();
    store = window.offlineStore;
});

beforeEach(async () => {
    now = START;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    document.body.dataset.offlineKeyMaxAge = "3600";
    document.body.dataset.sessionAge = "1800";
    document.body.innerHTML = '<div data-offline-key-expired hidden>Offline key expired</div>';
    await store.purgeAll();
    await window.crypto_session.clearSessionKey();
    await window.crypto_session.deriveSessionKey(PASSWORD, SALT);
    await store.saveOfflineEdit({ pk: PK, clientPk: "", localStatus: "modified", data: { note: "kept" } });
});

afterEach(() => vi.restoreAllMocks());

async function keepActive(at) {
    now = at;
    document.dispatchEvent(new Event("keydown"));
    // Wait for the public activity listener's IndexedDB write.
    await new Promise((resolve) => setTimeout(resolve, 10));
}

async function assertLocked() {
    expect(await store.getOfflineEvent(PK)).toBeNull();
    expect(window.crypto_session.hasSessionKey()).toBe(false);
    expect(await window.crypto_session.hasPersistedSessionKey()).toBe(false);
    expect(await store.db.events.count()).toBe(1);
    expect(document.querySelector("[data-offline-key-expired]").hidden).toBe(false);
}

describe("absolute key lifetime", () => {
    it("locks exactly at the deadline despite activity, preserving ciphertext for re-login", async () => {
        await keepActive(START + 1800_000);
        await keepActive(START + 3599_999);
        expect((await store.getOfflineEvent(PK)).data.note).toBe("kept");
        now = START + 3600_000;
        await assertLocked();
        await expect(store.saveOfflineEdit({ pk: PK, data: { note: "changed" } })).rejects.toMatchObject({ name: "NoSessionKeyError" });
        await window.crypto_session.deriveSessionKey(PASSWORD, SALT);
        expect((await store.getOfflineEvent(PK)).data.note).toBe("kept");
        expect(document.querySelector("[data-offline-key-expired]").hidden).toBe(true);
    });

    it("checks the key on the 60-second interval while input continues", async () => {
        await keepActive(START + 1800_000);
        await keepActive(START + 3600_000);
        checkInterval();
        await vi.waitFor(async () => expect(await window.crypto_session.hasPersistedSessionKey()).toBe(false));
        await assertLocked();
    });

    it("rejects a backwards clock rather than extending the lifetime", async () => {
        now = START + 600_000;
        expect((await store.getOfflineEvent(PK)).data.note).toBe("kept");
        now -= 1;
        await assertLocked();
    });

    it("expires on boot without refreshing the original creation time", async () => {
        await keepActive(START + 3600_000);
        vi.resetModules();
        const timer = vi.spyOn(window, "setInterval").mockReturnValue(1);
        await import("../../src/static/js/crypto.js");
        await window.crypto_session.ready();
        timer.mockRestore();
        expect(await window.crypto_session.hasPersistedSessionKey()).toBe(false);
        expect(await store.db.events.count()).toBe(1);
    });


    it("checks expiry before saving with a cached key", async () => {
        await keepActive(START + 3600_000);
        await expect(store.saveOfflineEdit({ pk: PK, data: { note: "changed" } })).rejects.toMatchObject({ name: "NoSessionKeyError" });
        expect(await window.crypto_session.hasPersistedSessionKey()).toBe(false);
        expect(await store.db.events.count()).toBe(1);
    });

    it("does not reset the stored cap when the page policy changes", async () => {
        document.body.dataset.offlineKeyMaxAge = "86400";
        await keepActive(START + 3600_000);
        await assertLocked();
    });

    it("expires legacy keys with missing lifetime metadata at boot", async () => {
        const db = await new Promise((resolve) => {
            const req = indexedDB.open("anlaufstelle-crypto", 1);
            req.onsuccess = () => resolve(req.result);
        });
        await new Promise((resolve) => {
            const tx = db.transaction("meta", "readwrite");
            const meta = tx.objectStore("meta");
            const read = meta.get("sessionKey");
            read.onsuccess = () => meta.put({ key: "sessionKey", cryptoKey: read.result.cryptoKey });
            tx.oncomplete = resolve;
        });
        db.close();
        vi.resetModules();
        vi.spyOn(window, "setInterval").mockReturnValue(1);
        await import("../../src/static/js/crypto.js");
        await window.crypto_session.ready();
        expect(await window.crypto_session.hasPersistedSessionKey()).toBe(false);
        expect(await store.db.events.count()).toBe(1);
        expect(document.querySelector("[data-offline-key-expired]").hidden).toBe(false);
    });

    it("still applies the idle window before the absolute deadline", async () => {
        now = START + 1800_001;
        await window.crypto_session.enforceIdleWipe();
        expect(window.crypto_session.hasSessionKey()).toBe(false);
        expect(await store.db.events.count()).toBe(1);
    });
});

describe("bundle expiry fails closed", () => {
    it.each([undefined, "", "not-a-date", new Date(START).toISOString()].flatMap(
        (expiry) => [[expiry, "read"], [expiry, "list"]]
    ))("rejects expired/invalid expires_at %s at the %s gate", async (expiry, gate) => {
        await store.saveClientBundle({
            client: { pk: PK, pseudonym: "Test" }, cases: [], events: [], workitems: [],
            schema_version: 2, generated_at: new Date(now).toISOString(), expires_at: expiry,
        });
        if (gate === "read") expect(await store.getOfflineClient(PK)).toBeNull();
        else expect(await store.listOfflineClientsDetailed()).toEqual([]);
    });
});
