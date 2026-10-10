// Refs #2528: public replay paths across independent pages sharing IndexedDB.
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import { JSDOM } from "jsdom";
import { IDBFactory, IDBKeyRange } from "fake-indexeddb";
import Dexie from "dexie";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const scripts = ["url-patterns", "crypto", "offline-db", "offline-bundles", "offline-revocations", "offline-store", "offline-queue", "offline-edit", "sync-orchestrator"]
    .map((name) => readFileSync(`src/static/js/${name}.js`, "utf8"));
const eventPk = "33333333-3333-4333-8333-333333333333";
const validPayload = () => ({ revocations: [], server_time: new Date().toISOString(), retention_seconds: 345600 });
let pages, status, payload, writes, checks, lockTail, channels;

function page(online = true) {
    const dom = new JSDOM('<body data-offline-key-max-age="43200"><div data-offline-revocation-pending hidden></div></body>', {
        url: "https://example.test/", runScripts: "outside-only",
    });
    pages.push(dom);
    const w = dom.window;
    // Keep automatic boot checks out of the controlled HTTP sequence.
    w.document.addEventListener("DOMContentLoaded", (event) => event.stopImmediatePropagation(), true);
    Object.defineProperty(w.document, "readyState", { value: "loading", configurable: true });
    Object.defineProperty(w, "crypto", { value: webcrypto });
    Object.assign(w, { Dexie, TextEncoder, TextDecoder, structuredClone, indexedDB: Dexie.dependencies.indexedDB, IDBKeyRange });
    Object.defineProperty(w.navigator, "onLine", { value: online, configurable: true });
    w.navigator.locks = { request: (_name, _options, fn) => {
        const result = lockTail.then(fn);
        lockTail = result.catch(() => {});
        return result;
    } };
    w.BroadcastChannel = class {
        constructor(name) { this.name = name; channels.push(this); }
        postMessage(data) {
            for (const channel of channels) {
                if (channel !== this && channel.name === this.name) channel.onmessage?.({ data });
            }
        }
        close() {}
    };
    w.fetch = async (url, options = {}) => {
        if (String(url).includes("/revocations/")) {
            checks += 1;
            return { status, ok: status === 200, json: async () => {
                if (payload === "not-json") throw new SyntaxError("Invalid JSON");
                return payload;
            } };
        }
        if (options.method === "POST") writes.push({ url, options });
        return { status: 201, ok: true, headers: { get: (name) => name.toLowerCase() === "x-offline-replay-result" ? "saved" : null } };
    };
    for (const script of scripts) w.eval(script);
    return w;
}

async function seed(w) {
    await w.crypto_session.ready();
    await w.crypto_session.deriveSessionKey("correct horse battery", "c2FsdHNhbHRzYWx0");
    await w.offlineQueue.enqueueRequest("/partials/tally/increment/", "POST", "contact=1", {});
    const record = { pk: eventPk, localStatus: "new", data: { formData: {}, idempotencyKey: "stable-edit-key" } };
    await w.offlineStore.saveOfflineEdit(record);
    return record;
}

async function directReplay(w, record) {
    await w.crypto_session.ready();
    await w.syncOrchestrator.runExclusive(async () => {
        await w.offlineQueue.replayQueue();
        await w.offlineEdit.replayModifiedEvent(record, { replay: true });
    });
}

beforeEach(() => {
    Dexie.dependencies.indexedDB = new IDBFactory();
    Dexie.dependencies.IDBKeyRange = IDBKeyRange;
    pages = []; channels = []; writes = []; checks = 0; status = 500; payload = validPayload();
    lockTail = Promise.resolve();
});

afterEach(async () => {
    for (const channel of channels) channel.onmessage = null;
    // Drain badge and status notifications before destroying their documents.
    await new Promise((resolve) => setTimeout(resolve, 30));
    for (const dom of pages) { dom.window.offlineStore.db.close(); dom.window.close(); }
});

describe("persistent revocation gate", () => {
    it.each(["navigation", "second tab"])("blocks direct queue and edit replay after failure across %s", async (mode) => {
        const a = page();
        const record = await seed(a);
        const b = mode === "second tab" ? page() : null;
        const beforeQueue = await a.offlineStore.db.queue.toArray();
        const beforeEvents = await a.offlineStore.db.events.toArray();
        await a.syncOrchestrator.requestSync("failed-check");
        const next = b || page();
        await directReplay(next, record);
        expect(checks).toBe(1);
        expect(writes).toHaveLength(0);
        expect(typeof beforeQueue[0].data.ct).toBe("string");
        expect(typeof beforeEvents[0].data.ct).toBe("string");
        expect(await next.offlineStore.db.queue.count()).toBe(1);
        expect(await next.offlineStore.db.events.count()).toBe(1);
        expect(await next.offlineStore.db.queue.toArray()).toEqual(beforeQueue);
        expect(await next.offlineStore.db.events.toArray()).toEqual(beforeEvents);
        expect(next.document.querySelector("[data-offline-revocation-pending]").hidden).toBe(false);
        status = 200;
        await next.syncOrchestrator.requestSync("valid-follow-up");
        expect(writes).toHaveLength(2);
        expect(await next.offlineStore.db.queue.count()).toBe(0);
        expect(writes.find(({ url }) => url.includes("tally")).options.headers["X-Idempotency-Key"]).toBeTruthy();
        expect(writes.find(({ url }) => url.includes("events")).options.headers["X-Idempotency-Key"]).toBe("stable-edit-key");
    });

    it("keeps fallback replay blocked when the coordinating module is absent", async () => {
        const w = page();
        const record = await seed(w);
        await w.syncOrchestrator.requestSync("failed-check");
        delete w.syncOrchestrator;
        await w.offlineQueue.replayQueue();
        await w.offlineEdit.replayModifiedEvent(record, { replay: true });
        expect(writes).toHaveLength(0);
        expect(await w.offlineStore.db.queue.count()).toBe(1);
        expect(await w.offlineStore.db.events.count()).toBe(1);
    });

    it("invalidates an earlier approval when persisting the next check fails", async () => {
        const w = page();
        const record = await seed(w);
        status = 200;
        expect((await w.offlineStore.applyClientRevocations()).status).toBe("ok");
        const put = vi.spyOn(w.offlineStore.db.meta, "put").mockRejectedValue(new Error("Storage write failed"));
        expect((await w.offlineStore.applyClientRevocations()).status).toBe("error");
        put.mockRestore();
        await directReplay(w, record);
        const next = page();
        await directReplay(next, record);
        expect(writes).toHaveLength(0);
        expect(checks).toBe(1);
        expect(await next.offlineStore.db.queue.count()).toBe(1);
        expect(await next.offlineStore.db.events.count()).toBe(1);
        await next.syncOrchestrator.requestSync("valid-retry");
        expect(writes).toHaveLength(2);
    });

    it("fails closed when the persisted decision cannot be read", async () => {
        const w = page();
        const record = await seed(w);
        status = 200;
        expect((await w.offlineStore.applyClientRevocations()).status).toBe("ok");
        w.offlineStore.db.meta.get = async () => { throw new Error("Storage unavailable"); };
        await directReplay(w, record);
        expect(writes).toHaveLength(0);
        expect(await w.offlineStore.db.queue.count()).toBe(1);
        expect(await w.offlineStore.db.events.count()).toBe(1);
    });

    it("does not let an older concurrent success release a newer failure", async () => {
        const a = page();
        const record = await seed(a);
        const b = page();
        let resolveFirst;
        a.fetch = async () => {
            checks += 1;
            return new Promise((resolve) => { resolveFirst = resolve; });
        };
        const first = a.offlineStore.applyClientRevocations();
        await vi.waitFor(() => expect(resolveFirst).toBeTypeOf("function"));
        expect((await b.offlineStore.applyClientRevocations()).status).toBe("error");
        resolveFirst({ status: 200, ok: true, json: async () => validPayload() });
        expect((await first).status).toBe("error");
        await directReplay(b, record);
        expect(writes).toHaveLength(0);
        expect(await b.offlineStore.db.queue.count()).toBe(1);
        expect(await b.offlineStore.db.events.count()).toBe(1);
    });

    it("requires a successful check even when no persisted decision exists", async () => {
        const w = page();
        const record = await seed(w);
        await directReplay(w, record);
        expect(writes).toHaveLength(0);
        expect(await w.offlineStore.db.queue.count()).toBe(1);
        expect(await w.offlineStore.db.events.count()).toBe(1);
    });
});

const invalidPayloads = [
    null, {}, [], "not-json", { revocations: "invalid" }, { revocations: null },
    { ...validPayload(), revocations: [null] }, { ...validPayload(), revocations: [123] },
    { ...validPayload(), revocations: ["not-a-uuid"] },
    { revocations: [], retention_seconds: 345600 },
    { ...validPayload(), server_time: 123 }, { ...validPayload(), server_time: "invalid" },
    { revocations: [], server_time: new Date().toISOString() },
    { ...validPayload(), retention_seconds: "345600" },
    { ...validPayload(), retention_seconds: 0 }, { ...validPayload(), retention_seconds: 1.5 },
];

describe("revocation response validation", () => {
    it.each(invalidPayloads.map((body, index) => [index, body]))("keeps both encrypted replay paths paused for invalid response %s", async (_index, body) => {
        const w = page();
        const record = await seed(w);
        status = 200; payload = body;
        const result = await w.offlineStore.applyClientRevocations();
        expect(result.status).toBe("error");
        await w.syncOrchestrator.requestSync("invalid-response");
        await directReplay(w, record);
        expect(writes).toHaveLength(0);
        expect(await w.offlineStore.db.queue.count()).toBe(1);
        expect(await w.offlineStore.db.events.count()).toBe(1);
        expect(w.document.querySelector("[data-offline-revocation-pending]").hidden).toBe(false);
        payload = validPayload();
        await w.syncOrchestrator.requestSync("valid-retry");
        expect(writes).toHaveLength(2);
    });
});
