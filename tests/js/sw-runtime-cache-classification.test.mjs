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
    const lifetime = [];
    const event = {
        request,
        respondWith: vi.fn((promise) => {
            capturedPromise = promise;
        }),
        waitUntil: vi.fn((promise) => {
            lifetime.push(Promise.resolve(promise));
        }),
    };
    handlers.fetch(event);
    if (capturedPromise) {
        await capturedPromise;
        // The revalidation side work is bound to the event lifetime (#2612).
        await Promise.all(lifetime);
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

    it.each([
        "text/css",
        "application/javascript",
        "text/javascript",
        "font/woff2",
        "image/svg+xml",
        "application/font-woff",
    ])("cacht erlaubten Asset-Content-Type %s", async (contentType) => {
        const spy = runtimeCacheSpy();
        const { handlers, fetch: fetchMock } = workerContext(spy.caches);
        fetchMock.mockResolvedValueOnce(buildFetchResponse({ contentType }));

        await dispatchFetch(handlers, `${ORIGIN}/static/js/app.js`);
        expect(spy.cache.put).toHaveBeenCalledTimes(1);
    });

    it.each([
        "text/css-malformed",
        "application/javascript-x",
        "text/javascriptx",
        "application/font-woff-x",
        "fontish/woff2",
        "imagery/svg+xml",
        "application/octet-stream",
    ])(
        "cacht nicht erlaubten oder praefixaehnlichen Content-Type %s nicht",
        async (contentType) => {
            const spy = runtimeCacheSpy();
            const { handlers, fetch: fetchMock } = workerContext(spy.caches);
            fetchMock.mockResolvedValueOnce(buildFetchResponse({ contentType }));

            await dispatchFetch(handlers, `${ORIGIN}/static/js/app.js`);
            expect(spy.cache.put).not.toHaveBeenCalled();
        },
    );

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

    it("cacht application/json ausschliesslich fuer /manifest.json", async () => {
        const manifestSpy = runtimeCacheSpy();
        const manifestWorker = workerContext(manifestSpy.caches);
        manifestWorker.fetch.mockResolvedValueOnce(buildFetchResponse({ contentType: "application/json" }));
        await dispatchFetch(manifestWorker.handlers, `${ORIGIN}/manifest.json`);
        expect(manifestSpy.cache.put).toHaveBeenCalledTimes(1);

        const staticSpy = runtimeCacheSpy();
        const staticWorker = workerContext(staticSpy.caches);
        staticWorker.fetch.mockResolvedValueOnce(buildFetchResponse({ contentType: "application/json" }));
        await dispatchFetch(staticWorker.handlers, `${ORIGIN}/static/data.json`);
        expect(staticSpy.cache.put).not.toHaveBeenCalled();
    });
});


// Refs #2612: the whole revalidation (network fetch, cache.put, budget trim)
// must be held by event.waitUntil, also when a cached copy answers at once, and
// cache failures must not surface as unhandled rejections.
function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => {
        resolve = res;
        reject = rej;
    });
    return { promise, resolve, reject };
}

function startFetch(handlers, url) {
    const lifetime = [];
    let responded;
    const event = {
        request: new Request(url),
        respondWith: vi.fn((promise) => {
            responded = promise;
        }),
        waitUntil: vi.fn((promise) => {
            lifetime.push(Promise.resolve(promise));
        }),
    };
    handlers.fetch(event);
    return { event, lifetime, responded: () => responded };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

async function settled(promise) {
    let done = false;
    promise.then(
        () => {
            done = true;
        },
        () => {
            done = true;
        },
    );
    await flush();
    return done;
}

describe("sw.js asset revalidation and event lifetime (#2612)", () => {
    it("holds the event open until cache.put and the trim of a cold asset finished", async () => {
        const spy = runtimeCacheSpy();
        const putGate = deferred();
        const keysGate = deferred();
        spy.cache.put.mockImplementation(() => putGate.promise);
        spy.cache.keys = vi.fn(() => keysGate.promise);
        const { handlers, fetch: fetchMock } = workerContext(spy.caches);
        fetchMock.mockResolvedValueOnce(buildFetchResponse());

        const { event, lifetime, responded } = startFetch(handlers, `${ORIGIN}/static/js/app.js`);
        const response = await responded();
        expect(response.status).toBe(200);

        expect(event.waitUntil).toHaveBeenCalledTimes(1);
        const hold = Promise.all(lifetime);
        expect(await settled(hold)).toBe(false);
        putGate.resolve();
        expect(await settled(hold)).toBe(false);
        keysGate.resolve([]);
        expect(await settled(hold)).toBe(true);
        expect(spy.cache.keys).toHaveBeenCalled();
    });

    it("holds the event open for the network revalidation behind a cache hit", async () => {
        const spy = runtimeCacheSpy();
        const netGate = deferred();
        spy.caches.match = async () => new Response("stale", { status: 200 });
        const { handlers, fetch: fetchMock } = workerContext(spy.caches);
        fetchMock.mockImplementationOnce(() => netGate.promise);

        const { event, lifetime, responded } = startFetch(handlers, `${ORIGIN}/static/js/app.js`);
        const response = await responded();
        expect(await response.text()).toBe("stale");

        expect(event.waitUntil).toHaveBeenCalledTimes(1);
        const hold = Promise.all(lifetime);
        expect(await settled(hold)).toBe(false);
        netGate.resolve(buildFetchResponse());
        expect(await settled(hold)).toBe(true);
        expect(spy.cache.put).toHaveBeenCalledTimes(1);
    });

    it("keeps the network response usable and handles a quota error on put", async () => {
        const spy = runtimeCacheSpy();
        const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
        const unhandled = vi.fn();
        process.on("unhandledRejection", unhandled);
        try {
            spy.cache.put.mockRejectedValue(new Error("QuotaExceededError"));
            const { handlers, fetch: fetchMock } = workerContext(spy.caches);
            fetchMock.mockResolvedValueOnce(buildFetchResponse());

            const { lifetime, responded } = startFetch(handlers, `${ORIGIN}/static/js/app.js`);
            const response = await responded();
            expect(response.status).toBe(200);
            expect(await response.text()).toBe("body");
            await expect(Promise.all(lifetime)).resolves.toBeDefined();
            await flush();
            expect(unhandled).not.toHaveBeenCalled();
            expect(warn).toHaveBeenCalled();
        } finally {
            process.off("unhandledRejection", unhandled);
            warn.mockRestore();
        }
    });

    it("handles a failing runtime cache open without breaking the response", async () => {
        const spy = runtimeCacheSpy();
        const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
        spy.caches.open = vi.fn(async () => {
            throw new Error("cache storage unavailable");
        });
        const { handlers, fetch: fetchMock } = workerContext(spy.caches);
        fetchMock.mockResolvedValueOnce(buildFetchResponse());

        const { lifetime, responded } = startFetch(handlers, `${ORIGIN}/static/js/app.js`);
        const response = await responded();
        expect(response.status).toBe(200);
        await expect(Promise.all(lifetime)).resolves.toBeDefined();
        warn.mockRestore();
    });

    it("does not hold the event open for uncacheable answers beyond the fetch itself", async () => {
        const spy = runtimeCacheSpy();
        const { handlers, fetch: fetchMock } = workerContext(spy.caches);
        fetchMock.mockResolvedValueOnce(buildFetchResponse({ contentType: "text/html" }));

        const { lifetime, responded } = startFetch(handlers, `${ORIGIN}/static/anything`);
        await responded();
        await expect(Promise.all(lifetime)).resolves.toBeDefined();
        expect(spy.cache.put).not.toHaveBeenCalled();
    });
});
