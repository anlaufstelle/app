// Refs #2528: real revocation fetch, encrypted queue and coordinated replay.
import { importOfflineStoreModules } from "./_offline_store_modules.mjs";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import Dexie from "dexie";

let store;
let failure;
let fetchSpy;
let editReplay;
let editApi;

beforeAll(async () => {
    window.Dexie = globalThis.Dexie = Dexie;
    document.head.innerHTML = '<meta name="csrf-token" content="fresh-token">';
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.ready();
    await window.crypto_session.deriveSessionKey("correct horse battery", "c2FsdHNhbHRzYWx0");
    await importOfflineStoreModules();
    store = window.offlineStore;
    await import("../../src/static/js/offline-queue.js");
    await import("../../src/static/js/offline-edit.js");
    editApi = window.offlineEdit;
    // Suppress boot heuristics; every sequence below uses requestSync.
    const readyState = vi.spyOn(document, "readyState", "get").mockReturnValue("loading");
    await import("../../src/static/js/sync-orchestrator.js");
    readyState.mockRestore();
});

beforeEach(async () => {
    await store.purgeAll();
    await window.crypto_session.deriveSessionKey("correct horse battery", "c2FsdHNhbHRzYWx0");
    document.body.innerHTML = '<div data-offline-revocation-pending hidden>Synchronisation wartet auf Widerrufsprüfung</div>';
    editReplay = vi.fn(async () => {});
    window.offlineEdit = { replayAllModifiedEvents: editReplay };
    failure = 500;
    fetchSpy = vi.fn(async (url) => {
        if (String(url).includes("/revocations/")) {
            if (failure === "network") throw new TypeError("Network failed");
            return { status: failure || 200, ok: !failure, json: async () => ({ revocations: [] }) };
        }
        return { status: 201, ok: true, headers: { get: (name) => name.toLowerCase() === "x-offline-replay-result" ? "saved" : null } };
    });
    vi.stubGlobal("fetch", fetchSpy);
    await window.offlineQueue.enqueueRequest("/partials/tally/increment/", "POST", "contact=1", {});
});

afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
});

describe("revocation gate", () => {
    it.each([500, 429, "network"])("pauses both replay paths on %s and retries exactly one queued contact", async (error) => {
        failure = error;
        await window.syncOrchestrator.requestSync("failed-check");
        expect(fetchSpy.mock.calls.filter(([url]) => url === "/partials/tally/increment/")).toHaveLength(0);
        expect(editReplay).not.toHaveBeenCalled();
        expect(await store.db.queue.count()).toBe(1);
        expect(document.querySelector("[data-offline-revocation-pending]").hidden).toBe(false);
        failure = null;
        await window.syncOrchestrator.requestSync("retry");
        await window.syncOrchestrator.requestSync("repeat");
        const contacts = fetchSpy.mock.calls.filter(([url]) => url === "/partials/tally/increment/");
        expect(contacts).toHaveLength(1);
        expect(contacts[0][1].headers["X-Idempotency-Key"]).toBeTruthy();
        expect(await store.db.queue.count()).toBe(0);
        expect(document.querySelector("[data-offline-revocation-pending]").hidden).toBe(true);
    });

    it("stops a second decrypted contact when the key expires during the first request", async () => {
        failure = null;
        await window.offlineQueue.enqueueRequest("/partials/tally/increment/", "POST", "contact=2", {});
        let now = Date.now();
        vi.spyOn(Date, "now").mockImplementation(() => now);
        const originalFetch = fetchSpy.getMockImplementation();
        fetchSpy.mockImplementation(async (url, options) => {
            const response = await originalFetch(url, options);
            if (url === "/partials/tally/increment/") now += 12 * 3600_000;
            return response;
        });
        await window.syncOrchestrator.requestSync("expiry-mid-replay");
        expect(fetchSpy.mock.calls.filter(([url]) => url === "/partials/tally/increment/")).toHaveLength(1);
        expect(await store.db.queue.count()).toBe(1);
        expect(await window.crypto_session.hasPersistedSessionKey()).toBe(false);
    });

    it("does not send an edit retry after expiry while refreshing CSRF", async () => {
        const record = { pk: "33333333-3333-4333-8333-333333333333", localStatus: "new",
            data: { formData: {}, idempotencyKey: "edit-retry" } };
        await store.saveOfflineEdit(record);
        let now = Date.now();
        vi.spyOn(Date, "now").mockImplementation(() => now);
        window.csrfUtils = { fromMeta: () => "token", refresh: async () => {
            now += 12 * 3600_000;
            return "fresh-token";
        } };
        fetchSpy.mockImplementation(async () => ({ status: 403, ok: false }));
        await editApi.replayModifiedEvent(record, { replay: true });
        expect(fetchSpy).toHaveBeenCalledTimes(1);
        expect(await window.crypto_session.hasPersistedSessionKey()).toBe(false);
        expect(await store.db.events.count()).toBe(1);
        delete window.csrfUtils;
    });

    it("blocks manual replay after a failed startup revocation check too", async () => {
        failure = null;
        await window.syncOrchestrator.requestSync("reset-status");
        await window.offlineQueue.enqueueRequest("/partials/tally/increment/", "POST", "contact=1", {});
        fetchSpy.mockClear();
        failure = 500;
        await window.syncOrchestrator._startupRevocationCheck();
        await window.offlineQueue.replayQueue();
        expect(fetchSpy.mock.calls.filter(([url]) => url === "/partials/tally/increment/")).toHaveLength(0);
        expect(await store.db.queue.count()).toBe(1);
        expect(document.querySelector("[data-offline-revocation-pending]").hidden).toBe(false);
    });

    it("also blocks manual queue and edit retries after a failed revocation check", async () => {
        await window.syncOrchestrator.requestSync("failed-check");
        await window.offlineQueue.replayQueue();
        const record = { pk: "33333333-3333-4333-8333-333333333333", localStatus: "new",
            data: { formData: {}, idempotencyKey: "manual-retry" } };
        await store.saveOfflineEdit(record);
        await editApi.replayModifiedEvent(record, { replay: true });
        expect(fetchSpy.mock.calls.filter(([url]) => url === "/partials/tally/increment/" || url === "/events/new/")).toHaveLength(0);
        expect(await store.db.queue.count()).toBe(1);
        expect(await store.db.events.count()).toBe(1);
    });

    it("also pauses on an unexpected revocation rejection", async () => {
        vi.spyOn(store, "applyClientRevocations").mockRejectedValue(new Error("Storage failed"));
        await window.syncOrchestrator.requestSync("rejected-check");
        expect(editReplay).not.toHaveBeenCalled();
        expect(await store.db.queue.count()).toBe(1);
        expect(document.querySelector("[data-offline-revocation-pending]").hidden).toBe(false);
    });
});
