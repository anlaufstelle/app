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
let pages, status, payload, writes, checks, lockTail, channels, responseOverrides;

function page(online = true) {
    const dom = new JSDOM('<body data-offline-key-max-age="43200"><div data-offline-revocation-pending hidden></div><div data-offline-auth-pending data-auth-pending-text="Sign in to sync {count} entries" hidden></div></body>', {
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
            }, ...responseOverrides };
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

function clientComponent(w) {
    let factory;
    w.Alpine = { data: (_name, create) => { factory = create; } };
    for (const name of ["offline-form-fields", "offline-client-view"]) {
        w.eval(readFileSync(`src/static/js/${name}.js`, "utf8"));
    }
    w.document.dispatchEvent(new w.Event("alpine:init"));
    const component = factory();
    component.wiValues = { title: "New task", item_type: "task", priority: "normal" };
    // Isolate rendering from the public save operation and its real replay.
    component.load = async () => {};
    component._reconcile = async () => {};
    return component;
}

beforeEach(() => {
    Dexie.dependencies.indexedDB = new IDBFactory();
    Dexie.dependencies.IDBKeyRange = IDBKeyRange;
    pages = []; channels = []; writes = []; checks = 0; status = 500; payload = validPayload();
    lockTail = Promise.resolve();
    responseOverrides = {};
});

afterEach(async () => {
    for (const channel of channels) channel.onmessage = null;
    // Drain badge and status notifications before destroying their documents.
    await new Promise((resolve) => setTimeout(resolve, 30));
    for (const dom of pages) { dom.window.offlineStore.db.close(); dom.window.close(); }
});

describe("persistent revocation gate", () => {
    it.each(["same page", "approved second tab", "navigation", "demo bootstrap"])("blocks both replay paths after all metadata writes fail across %s", async (mode) => {
        const a = page();
        const record = await seed(a);
        const b = mode === "approved second tab" ? page() : null;
        status = 200;
        await a.syncOrchestrator.runExclusive(async () => {
            expect((await a.offlineStore.applyClientRevocations()).status).toBe("ok");
        });
        if (b) {
            await b.crypto_session.ready();
            await b.syncOrchestrator.runExclusive(async () => {
                expect((await b.offlineStore.applyClientRevocations()).status).toBe("ok");
            });
        }
        const beforeQueue = await a.offlineStore.db.queue.toArray();
        const beforeEvents = await a.offlineStore.db.events.toArray();
        // Lost cross-tab notifications must not make an old approval reusable.
        for (const channel of channels) channel.onmessage = null;
        const put = vi.spyOn(a.offlineStore.db.meta, "put").mockRejectedValue(new Error("Metadata storage unavailable"));
        const remove = vi.spyOn(a.offlineStore.db.meta, "delete").mockRejectedValue(new Error("Metadata storage unavailable"));
        expect((await a.offlineStore.applyClientRevocations()).status).toBe("error");
        put.mockRestore(); remove.mockRestore();
        const next = mode === "same page" ? a : b || page();
        if (mode === "demo bootstrap") {
            const originalFetch = next.fetch;
            next.fetch = (url, options) => String(url).includes("/offline-key-salt/")
                ? Promise.resolve({ ok: true, json: async () => ({ salt: "c2FsdHNhbHRzYWx0", offline_key_max_age: 43200 }) })
                : originalFetch(url, options);
            next.document.body.insertAdjacentHTML("beforeend", '<div data-demo-offline-password="correct horse battery"></div>');
            const warm = vi.spyOn(next.offlineStore, "revalidateCachedFacility").mockResolvedValue({ ok: true });
            next.eval(readFileSync("src/static/js/auth-bootstrap.js", "utf8"));
            await vi.waitFor(() => expect(warm).toHaveBeenCalledTimes(1));
            expect(next.crypto_session.hasSessionKey()).toBe(true);
            expect(next.document.querySelector("[data-demo-offline-password]")).toBeNull();
        }
        await directReplay(a, record);
        await directReplay(next, record);
        expect(writes).toHaveLength(0);
        expect(await next.offlineStore.isRevocationPending()).toBe(true);
        expect(await next.offlineStore.db.queue.toArray()).toEqual(beforeQueue);
        expect(await next.offlineStore.db.events.toArray()).toEqual(beforeEvents);
        status = 500;
        await next.syncOrchestrator.requestSync("failed-follow-up");
        expect(writes).toHaveLength(0);
        status = 200; payload = {};
        await next.syncOrchestrator.requestSync("invalid-follow-up");
        expect(writes).toHaveLength(0);
        payload = validPayload();
        await next.syncOrchestrator.requestSync("fresh-valid-follow-up");
        expect(writes).toHaveLength(2);
        expect(writes.find(({ url }) => url.includes("tally")).options.headers["X-Idempotency-Key"]).toBe(beforeQueue[0].idempotencyKey);
        expect(writes.find(({ url }) => url.includes("events")).options.headers["X-Idempotency-Key"]).toBe("stable-edit-key");
        await next.syncOrchestrator.requestSync("repeat-follow-up");
        expect(writes).toHaveLength(2);
    });

    it("checks a fresh list under the lock when saving directly from the client view", async () => {
        const w = page();
        await seed(w);
        status = 200;
        await w.syncOrchestrator.runExclusive(() => w.offlineStore.applyClientRevocations());
        const before = checks;
        await clientComponent(w).saveCreateWorkItem();
        expect(checks).toBe(before + 1);
        expect(writes).toHaveLength(1);
        expect(writes[0].url).toBe("/workitems/new/");
    });

    it("does not replay a captured client record purged by the fresh revocation check", async () => {
        const w = page();
        await seed(w);
        status = 200;
        const component = clientComponent(w);
        component._pk = "11111111-1111-4111-8111-111111111111";
        payload = { ...validPayload(), revocations: [component._pk] };
        await component.saveCreateWorkItem();
        expect(writes).toHaveLength(0);
        expect(await w.offlineStore.db.events.where("clientPk").equals(component._pk).count()).toBe(0);
        expect(await w.offlineStore.db.queue.count()).toBe(1);
    });

    it("blocks a direct client save when all metadata writes fail in an approved second tab", async () => {
        const a = page();
        await seed(a);
        const b = page();
        await b.crypto_session.ready();
        status = 200;
        await b.syncOrchestrator.runExclusive(() => b.offlineStore.applyClientRevocations());
        const put = vi.spyOn(b.offlineStore.db.meta, "put").mockRejectedValue(new Error("Metadata storage unavailable"));
        const remove = vi.spyOn(b.offlineStore.db.meta, "delete").mockRejectedValue(new Error("Metadata storage unavailable"));
        const component = clientComponent(b);
        await component.saveCreateWorkItem();
        expect(writes).toHaveLength(0);
        expect(component.lastSyncResult).toBe("pending");
        expect(await b.offlineStore.db.queue.count()).toBe(1);
        expect(await b.offlineStore.db.events.count()).toBe(2);
        put.mockRestore(); remove.mockRestore();
        await b.syncOrchestrator.requestSync("fresh-retry-after-direct-save");
        expect(writes).toHaveLength(3);
    });

    it("does not reuse a successful check in a later exclusive replay run", async () => {
        const w = page();
        const record = await seed(w);
        status = 200;
        await w.syncOrchestrator.runExclusive(async () => {
            expect((await w.offlineStore.applyClientRevocations()).status).toBe("ok");
        });
        await directReplay(w, record);
        expect(writes).toHaveLength(0);
        await w.syncOrchestrator.runExclusive(async () => {
            expect((await w.offlineStore.applyClientRevocations()).status).toBe("ok");
            await w.offlineQueue.replayQueue();
            await w.offlineEdit.replayModifiedEvent(record, { replay: true });
        });
        expect(writes).toHaveLength(2);
    });

    it("keeps replay blocked when persisting the successful decision fails", async () => {
        const w = page();
        const record = await seed(w);
        status = 200;
        const originalPut = w.offlineStore.db.meta.put.bind(w.offlineStore.db.meta);
        const put = vi.spyOn(w.offlineStore.db.meta, "put").mockImplementation((row) => {
            if (row.value.pending === false) throw new Error("Approval storage unavailable");
            return originalPut(row);
        });
        await w.syncOrchestrator.requestSync("approval-storage-failure");
        await directReplay(w, record);
        expect(writes).toHaveLength(0);
        expect(await w.offlineStore.db.queue.count()).toBe(1);
        expect(await w.offlineStore.db.events.count()).toBe(1);
        put.mockRestore();
        await w.syncOrchestrator.requestSync("persisted-retry");
        expect(writes).toHaveLength(2);
    });

    it("requires a fresh check after a metadata read failure in the approved run", async () => {
        const w = page();
        const record = await seed(w);
        status = 200;
        await w.syncOrchestrator.runExclusive(async () => {
            expect((await w.offlineStore.applyClientRevocations()).status).toBe("ok");
            const get = vi.spyOn(w.offlineStore.db.meta, "get").mockRejectedValue(new Error("Metadata read unavailable"));
            await w.offlineQueue.replayQueue();
            await w.offlineEdit.replayModifiedEvent(record, { replay: true });
            expect(writes).toHaveLength(0);
            get.mockRestore();
            await w.offlineQueue.replayQueue();
            await w.offlineEdit.replayModifiedEvent(record, { replay: true });
            expect(writes).toHaveLength(0);
            expect((await w.offlineStore.applyClientRevocations()).status).toBe("ok");
            await w.offlineQueue.replayQueue();
            await w.offlineEdit.replayModifiedEvent(record, { replay: true });
            expect(writes).toHaveLength(2);
        });
    });

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

const authResponses = [
    ["followed login", "login", { redirected: true, url: "https://example.test/login/?next=/" }],
    ["followed MFA setup", "mfa", { redirected: true, url: "https://example.test/mfa/setup/" }],
    ["followed MFA verify", "mfa", { redirected: true, url: "https://example.test/mfa/verify/" }],
    ["login target", "login", { url: "https://example.test/login/" }],
    ["MFA target", "mfa", { url: "https://example.test/mfa" }],
    ["redirected HTML", "login", { redirected: true, url: "https://example.test/auth/", headers: { get: () => "text/html; charset=utf-8" } }],
];

describe("revocation authentication notice", () => {
    it.each(authResponses)("preserves work and requests authentication for %s", async (_name, kind, response) => {
        const w = page();
        const record = await seed(w);
        const beforeQueue = await w.offlineStore.db.queue.toArray();
        const beforeEvents = await w.offlineStore.db.events.toArray();
        status = 200; payload = "not-json"; responseOverrides = response;
        const result = await w.offlineStore.applyClientRevocations();
        expect(result).toMatchObject({ status: "auth-pending", kind, purgedClients: 0, purgedQueueEntries: 0, purgedDrafts: 0 });
        const authEvents = [];
        w.addEventListener("offline-auth-pending", (event) => authEvents.push(event.detail));
        await w.syncOrchestrator.requestSync("authentication-required");
        await vi.waitFor(() => expect(authEvents).toContainEqual({ pending: true, count: 2, kind }));
        expect(w.document.querySelector("[data-offline-auth-pending]").hidden).toBe(false);
        expect(w.document.querySelector("[data-offline-auth-pending]").textContent).toBe("Sign in to sync 2 entries");
        await directReplay(w, record);
        expect(await w.offlineStore.isRevocationPending()).toBe(true);
        expect(w.document.querySelector("[data-offline-revocation-pending]").hidden).toBe(true);
        expect(writes).toHaveLength(0);
        expect(await w.offlineStore.db.queue.toArray()).toEqual(beforeQueue);
        expect(await w.offlineStore.db.events.toArray()).toEqual(beforeEvents);
        expect((await w.offlineStore.db.meta.get("revocationReplayState")).value.pending).toBe(true);
        const next = page();
        await directReplay(next, record);
        expect(writes).toHaveLength(0);
        expect(await next.offlineStore.isRevocationPending()).toBe(true);
        status = 200; payload = validPayload(); responseOverrides = {};
        await w.syncOrchestrator.requestSync("authenticated-retry");
        await vi.waitFor(() => expect(w.document.querySelector("[data-offline-auth-pending]").hidden).toBe(true));
        await w.syncOrchestrator.requestSync("repeat-authenticated-retry");
        expect(writes).toHaveLength(2);
        expect(writes.find(({ url }) => url.includes("tally")).options.headers["X-Idempotency-Key"]).toBe(beforeQueue[0].idempotencyKey);
        expect(writes.find(({ url }) => url.includes("events")).options.headers["X-Idempotency-Key"]).toBe("stable-edit-key");
    });

    it.each([
        ["server failure", 500, validPayload(), {}, "error"],
        ["rate limit", 429, validPayload(), {}, "ratelimited"],
        ["invalid structure", 200, {}, {}, "error"],
        ["invalid JSON", 200, "not-json", {}, "error"],
        ["unredirected HTML", 200, "not-json", { headers: { get: () => "text/html" } }, "error"],
        ["redirected invalid JSON", 200, "not-json", { redirected: true, url: "https://example.test/unexpected/", headers: { get: () => "application/json" } }, "error"],
    ])("keeps the revocation notice for %s after an authentication pause", async (_name, code, body, response, expectedStatus) => {
        const w = page();
        const record = await seed(w);
        status = 200; payload = "not-json"; responseOverrides = authResponses[0][2];
        await w.syncOrchestrator.requestSync("authentication-required");
        await vi.waitFor(() => expect(w.document.querySelector("[data-offline-auth-pending]").hidden).toBe(false));
        status = code; payload = body; responseOverrides = response;
        expect((await w.offlineStore.applyClientRevocations()).status).toBe(expectedStatus);
        await w.syncOrchestrator.requestSync("failed-check");
        await directReplay(w, record);
        await vi.waitFor(() => expect(w.document.querySelector("[data-offline-auth-pending]").hidden).toBe(true));
        expect(w.document.querySelector("[data-offline-revocation-pending]").hidden).toBe(false);
        expect(writes).toHaveLength(0);
        expect(await w.offlineStore.db.queue.count()).toBe(1);
        expect(await w.offlineStore.db.events.count()).toBe(1);
    });
});
