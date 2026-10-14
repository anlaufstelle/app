// Refs #1904: after a service worker update no cache may still answer with the
// policy an earlier generation stored. Cached responses keep the headers they
// were fetched with, so the update path has to replace them: install fetches
// the whole precache anew, activate deletes every cache that does not belong
// to the current generation. The guard runs the real sw.js lifecycle against
// an in-memory Cache Storage. Activation keeps the runtime cache of an
// unchanged generation, so a policy change needs a new CACHE_NAME; the last
// test pins the generation that was shipped with the earlier policy.
//
// Real sw.js code in a VM context (pattern: sw-precache-selfheal.test.mjs).
import { readFileSync } from "node:fs";
import vm from "node:vm";

import { describe, expect, it } from "vitest";

const SW_SOURCE = readFileSync("src/static/js/sw.js", "utf8");
const URL_PATTERNS_SOURCE = readFileSync("src/static/js/url-patterns.js", "utf8");
const ORIGIN = "https://example.test";
const UUID = "11111111-2222-3333-4444-555555555555";

const OLD_POLICY = "default-src 'self'; frame-ancestors 'none'";
const NEW_POLICY = "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'";

// A cache generation name that can never be the current one.
const PREVIOUS_GENERATION = "anlaufstelle-v0";
// The last cache generation shipped with the policy before base-uri and
// form-action (fixed history, not the current name). Devices updating from it
// still hold its runtime cache with responses stored under that policy.
const LAST_GENERATION_WITH_EARLIER_POLICY = "anlaufstelle-v54";
// Loaded on every page (base.html), fetched at runtime, not precached.
const RUNTIME_ASSET = "/static/js/htmx.min.js";
const RUNTIME_ASSET_BODY = "/* runtime asset stored with the earlier policy */";

function absolute(request) {
    return typeof request === "string" ? new URL(request, ORIGIN).href : request.url;
}

// Cache semantics the worker relies on: match by URL (the worker matches
// shells by URL string), put replaces, addAll fetches everything first and
// stores all or nothing.
class MemoryCache {
    constructor(network) {
        this.network = network;
        this.entries = new Map();
    }

    async match(request) {
        const hit = this.entries.get(absolute(request));
        return hit ? hit.clone() : undefined;
    }

    async put(request, response) {
        this.entries.set(absolute(request), response.clone());
    }

    async addAll(urls) {
        const responses = await Promise.all(urls.map((url) => this.network(new Request(absolute(url)))));
        if (responses.some((response) => !response.ok)) {
            throw new TypeError("addAll: bad response");
        }
        urls.forEach((url, index) => this.entries.set(absolute(url), responses[index]));
    }

    async keys() {
        return [...this.entries.keys()].map((url) => new Request(url));
    }

    async delete(request) {
        return this.entries.delete(absolute(request));
    }
}

// CacheStorage.match searches the caches in creation order.
class MemoryCacheStorage {
    constructor(network) {
        this.network = network;
        this.byName = new Map();
    }

    async open(name) {
        if (!this.byName.has(name)) {
            this.byName.set(name, new MemoryCache(this.network));
        }
        return this.byName.get(name);
    }

    async keys() {
        return [...this.byName.keys()];
    }

    async delete(name) {
        return this.byName.delete(name);
    }

    async match(request) {
        for (const cache of this.byName.values()) {
            const hit = await cache.match(request);
            if (hit) {
                return hit;
            }
        }
        return undefined;
    }
}

function network() {
    const state = { online: true };
    const fetchImpl = async (request) => {
        if (!state.online) {
            throw new TypeError("offline");
        }
        return new Response(`<html>${new URL(request.url).pathname}</html>`, {
            headers: { "Content-Type": "text/html; charset=utf-8", "Content-Security-Policy": NEW_POLICY },
        });
    };
    return { state, fetchImpl };
}

function storedWithPolicy(policy, body = "<html>stored</html>") {
    return new Response(body, {
        headers: { "Content-Type": "text/html; charset=utf-8", "Content-Security-Policy": policy },
    });
}

function loadWorker(storage, fetchImpl) {
    const handlers = {};
    const context = {
        URL,
        URLSearchParams,
        Request,
        Response,
        Headers,
        AbortSignal,
        Promise,
        setTimeout,
        clearTimeout,
        crypto: { randomUUID: () => "minted" },
        fetch: fetchImpl,
        caches: storage,
        console,
        skipWaiting: () => {},
    };
    context.self = context;
    context.location = { origin: ORIGIN };
    context.addEventListener = (type, callback) => {
        handlers[type] = callback;
    };
    context.clients = { matchAll: async () => [], get: async () => null, claim: () => {} };
    context.importScripts = () => {
        vm.runInContext(URL_PATTERNS_SOURCE, sandbox);
    };
    const sandbox = vm.createContext(context);
    vm.runInContext(SW_SOURCE, sandbox);
    return {
        handlers,
        appShell: vm.runInContext("APP_SHELL", sandbox),
        cacheName: vm.runInContext("CACHE_NAME", sandbox),
        runtimeCacheName: vm.runInContext("RUNTIME_CACHE_NAME", sandbox),
    };
}

async function lifecycle(handlers, type) {
    const pending = [];
    handlers[type]({ waitUntil: (promise) => pending.push(Promise.resolve(promise)) });
    await Promise.all(pending);
}

async function respond(handlers, request) {
    let result = null;
    handlers.fetch({
        request,
        clientId: "client-1",
        respondWith(value) {
            result = Promise.resolve(value);
        },
    });
    return result;
}

// Every offline answer of the HTML branch: each in-place shell route, the
// generic cached-URL stage, the offline home and the HTMX banner.
const OFFLINE_REQUESTS = [
    ["client detail shell", `/clients/${UUID}/`, {}],
    ["conflict review shell", `/offline/conflicts/${UUID}/`, {}],
    ["event create shell", "/events/new/", {}],
    ["workitem create shell", "/workitems/new/", {}],
    ["client list shell", "/clients/", {}],
    ["workitem list shell", "/workitems/", {}],
    ["timeline shell", "/", {}],
    ["cached conflict list", "/offline/conflicts/", {}],
    ["offline home", "/offline/", {}],
    ["unknown page -> offline home", "/statistics/", {}],
    ["HTMX banner", "/clients/?q=a", { "HX-Request": "true" }],
];

async function offlinePolicies(handlers) {
    let languageTask;
    handlers.message({ data: { type: "SET_SHELL_LANGUAGE", language: "de" }, waitUntil: value => { languageTask = value; } });
    await languageTask;
    const delivered = {};
    for (const [label, path, headers] of OFFLINE_REQUESTS) {
        const response = await respond(
            handlers,
            new Request(`${ORIGIN}${path}`, { headers: { Accept: "text/html", ...headers } })
        );
        delivered[label] = response ? response.headers.get("Content-Security-Policy") : "no response";
    }
    return delivered;
}

async function storedPolicies(storage) {
    const found = [];
    for (const name of await storage.keys()) {
        const cache = await storage.open(name);
        for (const request of await cache.keys()) {
            const policy = (await cache.match(request)).headers.get("Content-Security-Policy");
            if (policy !== null) {
                found.push([name, new URL(request.url).pathname, policy]);
            }
        }
    }
    return found;
}

async function seed(storage, name, urls, policy) {
    const cache = await storage.open(name);
    for (const url of urls) {
        await cache.put(url, storedWithPolicy(policy));
    }
}

describe("service worker update replaces responses stored with an earlier policy", () => {
    it("new cache generation: old generations are deleted on activate", async () => {
        const net = network();
        const storage = new MemoryCacheStorage(net.fetchImpl);
        const worker = loadWorker(storage, net.fetchImpl);
        expect(worker.cacheName).not.toBe(PREVIOUS_GENERATION);
        // Installation of the previous generation: complete precache plus a
        // runtime entry, all stored with the old policy. Created first, so
        // CacheStorage.match would find them first.
        await seed(storage, PREVIOUS_GENERATION, worker.appShell, OLD_POLICY);
        await seed(storage, `${PREVIOUS_GENERATION}-runtime`, ["/static/icons/badge.svg"], OLD_POLICY);

        await lifecycle(worker.handlers, "install");
        await lifecycle(worker.handlers, "activate");
        net.state.online = false;

        const remaining = await storage.keys();
        expect(remaining).not.toContain(PREVIOUS_GENERATION);
        expect(remaining).not.toContain(`${PREVIOUS_GENERATION}-runtime`);
        expect(remaining.every((name) => [worker.cacheName, worker.runtimeCacheName].includes(name))).toBe(true);
        for (const [label, policy] of Object.entries(await offlinePolicies(worker.handlers))) {
            expect(policy, label).toBe(NEW_POLICY);
        }
        expect((await storedPolicies(storage)).filter(([, , policy]) => policy !== NEW_POLICY)).toEqual([]);
    });

    it("same cache generation, changed worker: install fetches every precache entry anew", async () => {
        const net = network();
        const storage = new MemoryCacheStorage(net.fetchImpl);
        const worker = loadWorker(storage, net.fetchImpl);
        await seed(storage, worker.cacheName, worker.appShell, OLD_POLICY);

        await lifecycle(worker.handlers, "install");
        await lifecycle(worker.handlers, "activate");
        net.state.online = false;

        for (const [label, policy] of Object.entries(await offlinePolicies(worker.handlers))) {
            expect(policy, label).toBe(NEW_POLICY);
        }
        expect((await storedPolicies(storage)).filter(([, , policy]) => policy !== NEW_POLICY)).toEqual([]);
    });

    it("pages, HTMX partials and API answers are never written to any cache", async () => {
        const net = network();
        const storage = new MemoryCacheStorage(net.fetchImpl);
        const worker = loadWorker(storage, net.fetchImpl);
        await lifecycle(worker.handlers, "install");
        await lifecycle(worker.handlers, "activate");
        const before = await storedPolicies(storage);

        // Online answers of the HTML branch, an HTMX partial and an API GET.
        for (const [path, headers] of [
            ["/clients/", { Accept: "text/html" }],
            [`/clients/${UUID}/`, { Accept: "text/html" }],
            ["/clients/?q=a", { Accept: "text/html", "HX-Request": "true" }],
            ["/api/offline/bundle/", { Accept: "application/json" }],
        ]) {
            const response = await respond(worker.handlers, new Request(`${ORIGIN}${path}`, { headers }));
            if (response) {
                await response.text();
            }
        }
        await new Promise((resolve) => setTimeout(resolve, 0));

        expect(await storedPolicies(storage)).toEqual(before);
        expect(await storage.keys()).toEqual([worker.cacheName]);
    });

    it("runtime entry stored with the earlier policy is not served after the update", async () => {
        const net = network();
        const storage = new MemoryCacheStorage(net.fetchImpl);
        const worker = loadWorker(storage, net.fetchImpl);
        expect(worker.appShell).not.toContain(RUNTIME_ASSET);
        // Installation of the generation shipped with the earlier policy: its
        // precache plus a legitimate runtime asset, both under that policy.
        await seed(storage, LAST_GENERATION_WITH_EARLIER_POLICY, worker.appShell, OLD_POLICY);
        const runtime = await storage.open(`${LAST_GENERATION_WITH_EARLIER_POLICY}-runtime`);
        await runtime.put(
            RUNTIME_ASSET,
            new Response(RUNTIME_ASSET_BODY, {
                headers: { "Content-Type": "application/javascript", "Content-Security-Policy": OLD_POLICY },
            })
        );

        await lifecycle(worker.handlers, "install");
        await lifecycle(worker.handlers, "activate");

        expect((await storedPolicies(storage)).filter(([, , policy]) => policy !== NEW_POLICY)).toEqual([]);
        // Offline and online (stale-while-revalidate answers a cache hit
        // first): the old entry is gone, neither answer is the stored one.
        for (const online of [false, true]) {
            net.state.online = online;
            const response = await respond(worker.handlers, new Request(`${ORIGIN}${RUNTIME_ASSET}`));
            expect(response.headers.get("Content-Security-Policy"), `online=${online}`).not.toBe(OLD_POLICY);
            expect(await response.text(), `online=${online}`).not.toBe(RUNTIME_ASSET_BODY);
        }
    });
});
