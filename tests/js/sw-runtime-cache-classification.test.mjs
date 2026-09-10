// Refs #2201: Service-Worker-Asset-Klassifikation im Runtime-Cache.
import { readFileSync } from "node:fs";
import vm from "node:vm";

import { describe, expect, it, vi } from "vitest";

const SW_SOURCE = readFileSync("src/static/js/sw.js", "utf8");
const URL_PATTERNS_SOURCE = readFileSync("src/static/js/url-patterns.js", "utf8");
const ORIGIN = "https://example.test";

function workerContext(cachesImpl) {
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
        fetch: vi.fn(async () => new Response("net")),
        caches: cachesImpl,
        console,
        skipWaiting: vi.fn(),
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
    return { handlers, sandbox, fetch: context.fetch };
}

function runtimeCacheSpy() {
    const stored = [];
    const cache = {
        match: vi.fn(async () => undefined),
        addAll: vi.fn(async () => {}),
        keys: async () => [],
        put: vi.fn(async (request, response) => {
            stored.push({ url: request.url, contentType: response.headers.get("content-type") });
        }),
        delete: async () => true,
    };
    const caches = {
        open: vi.fn(async () => cache),
        match: async () => undefined,
        keys: async () => [],
        delete: async () => true,
    };
    return { cache, caches, stored };
}

function buildFetchResponse({ status = 200, contentType = "application/javascript" } = {}) {
    return new Response("body", { status, headers: { "content-type": contentType } });
}

async function dispatchFetch(handlers, url, opts = {}) {
    const request = new Request(url, opts);
    let capturedPromise;
    const event = {
        request,
        respondWith: vi.fn((promise) => {
            capturedPromise = promise;
        }),
    };
    handlers.fetch(event);
    if (capturedPromise) {
        await capturedPromise;
        // Der SWR-Zweig chained cache.put asynchron; kurz warten, damit der Spy erfasst wird.
        await new Promise((resolve) => setTimeout(resolve, 10));
    }
    return event;
}

describe("sw.js Runtime-Cache-Klassifikation", () => {
    it("cacht /static/ URLs nur per pathname, nicht Query-String", async () => {
        const spy = runtimeCacheSpy();
        const { handlers, fetch: fetchMock } = workerContext(spy.caches);
        fetchMock.mockResolvedValueOnce(buildFetchResponse({ contentType: "application/javascript" }));

        await dispatchFetch(handlers, `${ORIGIN}/events/?q=/static/foo.js`);
        expect(spy.cache.put).not.toHaveBeenCalled();
    });

    it("ignoriert /static/ auch im Fragment", async () => {
        const spy = runtimeCacheSpy();
        const { handlers, fetch: fetchMock } = workerContext(spy.caches);
        fetchMock.mockResolvedValueOnce(buildFetchResponse());

        await dispatchFetch(handlers, `${ORIGIN}/events/#/static/foo.js`);
        expect(spy.cache.put).not.toHaveBeenCalled();
    });

    it("cacht echte /static/ Assets mit passendem Content-Type", async () => {
        const spy = runtimeCacheSpy();
        const { handlers, fetch: fetchMock } = workerContext(spy.caches);
        fetchMock.mockResolvedValueOnce(buildFetchResponse({ contentType: "application/javascript" }));

        await dispatchFetch(handlers, `${ORIGIN}/static/js/app.js`);
        expect(spy.cache.put).toHaveBeenCalledTimes(1);
    });

    it("cacht fremde Origin mit /static/ Pfad nicht", async () => {
        const spy = runtimeCacheSpy();
        const { handlers, fetch: fetchMock } = workerContext(spy.caches);
        fetchMock.mockResolvedValueOnce(buildFetchResponse({ contentType: "application/javascript" }));

        await dispatchFetch(handlers, "https://other.example/static/js/app.js");
        expect(spy.cache.put).not.toHaveBeenCalled();
    });

    it("cacht text/html Antwort auf /static/ Pfad nicht", async () => {
        const spy = runtimeCacheSpy();
        const { handlers, fetch: fetchMock } = workerContext(spy.caches);
        fetchMock.mockResolvedValueOnce(buildFetchResponse({ contentType: "text/html" }));

        await dispatchFetch(handlers, `${ORIGIN}/static/anything`);
        expect(spy.cache.put).not.toHaveBeenCalled();
    });

    it("respektiert Cache-Control: no-store", async () => {
        const spy = runtimeCacheSpy();
        const { handlers, fetch: fetchMock } = workerContext(spy.caches);
        fetchMock.mockResolvedValueOnce(
            new Response("body", {
                status: 200,
                headers: { "content-type": "application/javascript", "cache-control": "no-store" },
            }),
        );

        await dispatchFetch(handlers, `${ORIGIN}/static/js/app.js`);
        expect(spy.cache.put).not.toHaveBeenCalled();
    });

    it("respektiert Cache-Control-Direktiven case-insensitiv", async () => {
        const spy = runtimeCacheSpy();
        const { handlers, fetch: fetchMock } = workerContext(spy.caches);
        fetchMock.mockResolvedValueOnce(
            new Response("body", {
                status: 200,
                headers: { "content-type": "application/javascript", "cache-control": "Private, No-Store" },
            }),
        );

        await dispatchFetch(handlers, `${ORIGIN}/static/js/app.js`);
        expect(spy.cache.put).not.toHaveBeenCalled();
    });

    it("cacht Antworten auf HX-Requests nicht", async () => {
        const spy = runtimeCacheSpy();
        const { handlers, fetch: fetchMock } = workerContext(spy.caches);
        fetchMock.mockResolvedValueOnce(buildFetchResponse());

        await dispatchFetch(handlers, `${ORIGIN}/static/js/app.js`, { headers: { "HX-Request": "" } });
        expect(spy.cache.put).not.toHaveBeenCalled();
    });

    it("cacht manifest JSON-Content-Types nur fuer /manifest.json", async () => {
        const spy = runtimeCacheSpy();
        const { handlers, fetch: fetchMock } = workerContext(spy.caches);
        fetchMock.mockResolvedValueOnce(buildFetchResponse({ contentType: "application/manifest+json" }));

        await dispatchFetch(handlers, `${ORIGIN}/static/not-a-manifest.json`);
        expect(spy.cache.put).not.toHaveBeenCalled();
    });

    it("cacht /manifest.json weiterhin", async () => {
        const spy = runtimeCacheSpy();
        const { handlers, fetch: fetchMock } = workerContext(spy.caches);
        fetchMock.mockResolvedValueOnce(buildFetchResponse({ contentType: "application/manifest+json" }));

        await dispatchFetch(handlers, `${ORIGIN}/manifest.json`);
        expect(spy.cache.put).toHaveBeenCalledTimes(1);
    });
});
