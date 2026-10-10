// Refs #2528: exercise both public actions with real crypto, storage and replay.
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import { JSDOM } from "jsdom";
import { IDBFactory, IDBKeyRange } from "fake-indexeddb";
import Dexie from "dexie";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const scripts = ["url-patterns", "crypto", "offline-db", "offline-bundles", "offline-revocations", "offline-store", "offline-queue", "offline-edit", "sync-orchestrator", "offline-form-fields", "conflict-resolver"]
    .map((name) => readFileSync(`src/static/js/${name}.js`, "utf8"));
const eventPk = "33333333-3333-4333-8333-333333333333";
const clientPk = "44444444-4444-4444-8444-444444444444";
const updatedAt = "2026-10-10T08:01:00Z";
const waitingMessage = "Synchronisation wartet auf Widerrufsprüfung";
const validPayload = () => ({ revocations: [], server_time: new Date().toISOString(), retention_seconds: 345600 });
let dom, w, factory, status, payload, writes, checks, failFetch;

beforeEach(async () => {
    Dexie.dependencies.indexedDB = new IDBFactory();
    Dexie.dependencies.IDBKeyRange = IDBKeyRange;
    dom = new JSDOM('<body data-offline-key-max-age="43200"><meta name="csrf-token" content="token"><div data-offline-revocation-pending hidden></div></body>', {
        url: "https://example.test/", runScripts: "outside-only",
    });
    w = dom.window;
    // Drive the boot check explicitly to keep the request sequence deterministic.
    w.document.addEventListener("DOMContentLoaded", (event) => event.stopImmediatePropagation(), true);
    Object.defineProperty(w.document, "readyState", { value: "loading", configurable: true });
    Object.defineProperty(w, "crypto", { value: webcrypto });
    Object.assign(w, { Dexie, TextEncoder, TextDecoder, structuredClone, indexedDB: Dexie.dependencies.indexedDB, IDBKeyRange });
    Object.defineProperty(w.navigator, "onLine", { value: true, configurable: true });
    let lockTail = Promise.resolve();
    w.navigator.locks = { request: (_name, _options, fn) => {
        const result = lockTail.then(fn);
        lockTail = result.catch(() => {});
        return result;
    } };
    w.gettext = (message) => message;
    w.Alpine = { data: (name, create) => { if (name === "conflictResolver") factory = create; } };
    status = 200; payload = validPayload(); writes = []; checks = 0; failFetch = false;
    w.fetch = async (url, options = {}) => {
        if (String(url).includes("/revocations/")) {
            checks += 1;
            if (failFetch) throw new TypeError("Failed to fetch");
            return { status, ok: status === 200, json: async () => payload };
        }
        if (options.method === "POST") writes.push({ url: String(url), options });
        return { status: 200, ok: true, headers: { get: (name) => name.toLowerCase() === "x-offline-replay-result" ? "saved" : null } };
    };
    for (const script of scripts) w.eval(script);
    w.document.dispatchEvent(new w.Event("alpine:init"));
    await w.crypto_session.ready();
    await w.crypto_session.deriveSessionKey("test password", "c2FsdHNhbHRzYWx0");
    await w.offlineStore.saveOfflineEdit({
        pk: eventPk, clientPk, localStatus: "conflict",
        data: {
            formData: { note: "local value", selection: ["a", "b"] },
            expectedUpdatedAt: "2026-10-10T08:00:00Z",
            serverState: { data_json: { note: "server value", selection: ["c"] }, updated_at: updatedAt },
        },
    });
    await w.syncOrchestrator._startupRevocationCheck();
    expect(checks).toBe(1);
});

afterEach(async () => {
    vi.restoreAllMocks();
    // Let count/status notifications settle before closing their document.
    await new Promise((resolve) => setTimeout(resolve, 30));
    w.offlineStore.db.close();
    dom.window.close();
});

async function component() {
    const resolver = factory();
    resolver.eventPk = eventPk;
    await resolver.load();
    expect(resolver.showForm).toBe(true);
    resolver.diffRows.find((row) => row.key === "selection").choice = "server";
    return resolver;
}

function expectedData(action) {
    return { note: "local value", selection: action === "keepMerged" ? ["c"] : ["a", "b"] };
}

describe.each(["keepLocal", "keepMerged"])("Conflict action %s", (action) => {
    it("checks freshly and posts exactly once with the chosen raw values and current token", async () => {
        const resolver = await component();
        await w.offlineQueue.enqueueRequest("/partials/tally/increment/", "POST", "contact=1", {});
        await resolver[action]();
        expect(writes).toHaveLength(1);
        expect(checks).toBe(2);
        expect(writes[0].url).toBe(`/events/${eventPk}/edit/`);
        const body = new URLSearchParams(writes[0].options.body);
        expect(body.get("expected_updated_at")).toBe(updatedAt);
        expect(body.get("note")).toBe("local value");
        expect(body.getAll("selection")).toEqual(expectedData(action).selection);
        expect(resolver.resolved).toBe(true);
        expect(resolver.showResolved).toBe(true);
        expect((await w.offlineStore.getOfflineEvent(eventPk)).localStatus).toBe("clean");
        expect(await w.offlineStore.db.queue.count()).toBe(1);
        // Approval for the completed action cannot authorize a later replay.
        await w.syncOrchestrator.runExclusive(() => w.offlineQueue.replayQueue());
        expect(writes).toHaveLength(1);
    });

    it.each(["http-500", "http-429", "invalid-body", "fetch-error", "metadata-write", "approval-write", "metadata-read", "late-metadata-read"])(
        "keeps encrypted choices and an actionable form while %s blocks replay, then retries once",
        async (failure) => {
            const resolver = await component();
            const meta = w.offlineStore.db.meta;
            if (failure === "http-500") status = 500;
            if (failure === "http-429") status = 429;
            if (failure === "invalid-body") payload = { revocations: "invalid" };
            if (failure === "fetch-error") failFetch = true;
            if (failure === "metadata-write") {
                vi.spyOn(meta, "put").mockRejectedValue(new Error("Metadata unavailable"));
                vi.spyOn(meta, "delete").mockRejectedValue(new Error("Metadata unavailable"));
            }
            if (failure === "approval-write") {
                const put = meta.put.bind(meta);
                vi.spyOn(meta, "put").mockImplementation((row) => {
                    if (row.value.pending === false) throw new Error("Approval unavailable");
                    return put(row);
                });
            }
            if (failure === "metadata-read") vi.spyOn(meta, "get").mockRejectedValue(new Error("Metadata unreadable"));
            if (failure === "late-metadata-read") {
                const get = w.offlineStore.getOfflineEvent.bind(w.offlineStore);
                vi.spyOn(w.offlineStore, "getOfflineEvent").mockImplementation(async (pk) => {
                    const record = await get(pk);
                    if (checks === 2) vi.spyOn(meta, "get").mockRejectedValue(new Error("Metadata unreadable"));
                    return record;
                });
            }
            await resolver[action]();
            expect(writes).toHaveLength(0);
            expect(resolver.statusMessage).toBe(waitingMessage);
            expect(resolver.error).toBe("");
            expect(resolver.resolved).toBe(false);
            expect(resolver.resolving).toBe(false);
            expect(resolver.showForm).toBe(true);
            expect((await w.offlineStore.getOfflineEvent(eventPk)).data.formData).toEqual(expectedData(action));
            expect(JSON.stringify(await w.offlineStore.db.events.toArray())).not.toContain("local value");
            vi.restoreAllMocks();
            status = 200; payload = validPayload(); failFetch = false;
            const checksBeforeRetry = checks;
            await resolver[action]();
            expect(checks).toBe(checksBeforeRetry + 1);
            expect(writes).toHaveLength(1);
            expect(resolver.resolved).toBe(true);
            expect(resolver.statusMessage).toBe("");
        }
    );

    it("never posts a captured record purged by the fresh list", async () => {
        const resolver = await component();
        payload = { ...validPayload(), revocations: [clientPk] };
        await resolver[action]();
        expect(checks).toBe(2);
        expect(writes).toHaveLength(0);
        expect(await w.offlineStore.getOfflineEvent(eventPk)).toBeNull();
        expect(resolver.resolved).toBe(false);
        expect(resolver.statusMessage).toBe("Der lokale Datensatz ist nicht mehr verfügbar. Bitte die Konfliktübersicht neu laden.");
        expect(resolver.error).toBe("");
    });

    it("coalesces repeated clicks into one write", async () => {
        const resolver = await component();
        await Promise.all([resolver[action](), resolver[action]()]);
        expect(writes).toHaveLength(1);
        expect(checks).toBe(2);
        expect(resolver.resolved).toBe(true);
    });
});
