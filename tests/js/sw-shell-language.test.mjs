import { readFileSync } from "node:fs";
import vm from "node:vm";

import { describe, expect, it, vi } from "vitest";

const SW_SOURCE = readFileSync("src/static/js/sw.js", "utf8");
const URL_PATTERNS_SOURCE = readFileSync("src/static/js/url-patterns.js", "utf8");
const ORIGIN = "https://example.test";
const UUID = "11111111-2222-3333-4444-555555555555";

function workerContext(fetchImpl, cachesImpl, clientsImpl) {
    const handlers = {};
    const queued = [];
    class FakeMessageChannel {
        constructor() {
            this.port1 = { onmessage: null };
            this.port2 = { peer: this.port1 };
        }
    }
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
        MessageChannel: FakeMessageChannel,
        crypto: { randomUUID: vi.fn(() => "minted-sw-key") },
        fetch: fetchImpl,
        caches: cachesImpl,
        console,
    };
    context.self = context;
    context.location = { origin: ORIGIN };
    context.addEventListener = (type, callback) => {
        handlers[type] = callback;
    };
    context.clients = clientsImpl || {
        get: async () => ({
            postMessage(payload, ports) {
                queued.push(payload);
                queueMicrotask(() => ports[0].peer.onmessage({ data: { type: "QUEUE_ACK" } }));
            },
        }),
        matchAll: async () => [],
        claim: () => {},
    };
    context.importScripts = () => {
        vm.runInContext(URL_PATTERNS_SOURCE, sandbox);
    };
    const sandbox = vm.createContext(context);
    vm.runInContext(SW_SOURCE, sandbox);
    return { handlers, queued, context };
}

function dispatchFetch(handler, request) {
    let responsePromise = null;
    handler({
        request,
        clientId: "client-1",
        respondWith(value) {
            responsePromise = Promise.resolve(value);
        },
    });
    return responsePromise;
}

function cachesWith(entries) {
    return {
        match: vi.fn(async (key) => {
            const name = typeof key === "string" ? key : key.url;
            return name in entries ? new Response("cached:" + entries[name]) : undefined;
        }),
        open: vi.fn(async () => ({
            keys: async () => [],
            put: async () => {},
            delete: async () => true,
            addAll: async () => {},
        })),
        keys: async () => [],
        delete: async () => true,
    };
}

const offlineFetch = () => vi.fn(async () => Promise.reject(new TypeError("offline")));

function navRequest(url, headers) {
    return new Request(url, { headers: { Accept: "text/html", ...headers } });
}

const ALL_SHELLS = {
    "/offline/": "fallback-home",
    "/offline/client-shell/": "client-shell",
    "/offline/conflict-shell/": "conflict-shell",
    "/offline/event-shell/": "event-shell",
    "/offline/workitem-shell/": "workitem-shell",
    "/offline/clients/": "client-list-shell",
    "/offline/workitems/": "workitem-list-shell",
    "/offline/zeitstrom/": "zeitstrom-shell",
};

async function offlineNavigate(url, entries = ALL_SHELLS, headers = {}) {
    const { handlers } = workerContext(offlineFetch(), cachesWith(entries));
    const response = await dispatchFetch(handlers.fetch, navRequest(url, headers));
    return { status: response.status, body: await response.text(), response };
}


describe("account language for cached shells", () => {
    it("precaches both public language variants during installation", async () => {
        const addAll = vi.fn(async () => {});
        const { handlers } = workerContext(offlineFetch(), { open: async () => ({ addAll }) });
        let task;
        handlers.install({ waitUntil: value => { task = value; } });
        await task;
        expect(addAll.mock.calls[0][0]).toContain("/offline/event-shell/?shell_language=en");
        expect(addAll.mock.calls[0][0]).toContain("/offline/event-shell/?shell_language=de");
    });

    it("uses the latest account language and survives worker restarts", async () => {
        const entries = new Map([
            ["/offline/event-shell/", new Response("German legacy shell")],
            ["/offline/event-shell/?shell_language=en", new Response("English shell")],
            ["/offline/event-shell/?shell_language=de", new Response("German shell")],
        ]);
        const cache = {
            match: async key => entries.get(key)?.clone(),
            put: async (key, response) => { entries.set(key, response); },
            addAll: async () => {},
        };
        const names = new Set();
        const caches = {
            open: async name => { names.add(name); return cache; }, match: cache.match,
            keys: async () => [...names], delete: async name => names.delete(name),
        };
        let worker = workerContext(offlineFetch(), caches);
        const setLanguage = async language => {
            const tasks = [];
            worker.handlers.message({ data: { type: "SET_SHELL_LANGUAGE", language }, waitUntil: value => tasks.push(value) });
            await Promise.all(tasks);
        };
        await setLanguage("en");
        worker = workerContext(offlineFetch(), caches);
        expect(await (await dispatchFetch(worker.handlers.fetch, navRequest(`${ORIGIN}/events/new/`))).text()).toBe("English shell");
        await setLanguage("de");
        expect(await (await dispatchFetch(worker.handlers.fetch, navRequest(`${ORIGIN}/events/new/`))).text()).toBe("German shell");
    });

    it("does not return another language when the selected variant is missing", async () => {
        const entries = { "/offline/shell-language/": "en", "/offline/event-shell/": "German shell" };
        const caches = { match: async key => entries[key] ? new Response(entries[key]) : undefined };
        const { handlers } = workerContext(offlineFetch(), caches);
        const response = await dispatchFetch(handlers.fetch, navRequest(`${ORIGIN}/events/new/`));
        expect(response.status).toBe(503);
        expect(await response.text()).not.toContain("German shell");
    });
});

function preferenceCache(entries, overrides = {}) {
    const cache = {
        match: async key => entries.get(key)?.clone(),
        put: vi.fn(async (key, response) => { entries.set(key, response); }),
        addAll: vi.fn(async () => {}), ...overrides,
    };
    const names = new Set();
    return { cache, names, caches: {
        open: async name => { names.add(name); return cache; }, match: cache.match,
        keys: async () => [...names], delete: async name => names.delete(name),
    } };
}
function message(worker, data) {
    let task;
    worker.handlers.message({ data, waitUntil: value => { task = value; } });
    return task;
}
it("a cached English shell is immediate while unrelated repair hangs", async () => {
    const { cache, caches } = preferenceCache(new Map([
        ["/offline/shell-language/", new Response("de")],
        ["/offline/event-shell/?shell_language=en", new Response("English shell")],
    ]), { addAll: vi.fn(() => new Promise(() => {})) });
    const worker = workerContext(offlineFetch(), caches);
    message(worker, { type: "SET_SHELL_LANGUAGE", language: "en" });
    await vi.waitFor(() => expect(cache.addAll).toHaveBeenCalled());
    const result = await Promise.race([
        dispatchFetch(worker.handlers.fetch, navRequest(`${ORIGIN}/events/new/`)).then(r => r.text()),
        new Promise(resolve => setTimeout(() => resolve("BLOCKED_ON_REPAIR"), 100)),
    ]);
    expect(result).toBe("English shell");
});
it.each(["en", "de"])("%s takes effect immediately despite failed persistence and retries on CHECK_PRECACHE", async language => {
    const previous = language === "en" ? "de" : "en";
    const entries = new Map([
        ["/offline/shell-language/", new Response(previous)],
        ["/offline/event-shell/?shell_language=en", new Response("English shell")],
        ["/offline/event-shell/?shell_language=de", new Response("German shell")],
    ]);
    const { cache, caches } = preferenceCache(entries);
    cache.put.mockRejectedValueOnce(new Error("Cache storage write failed"));
    const worker = workerContext(offlineFetch(), caches);
    await message(worker, { type: "SET_SHELL_LANGUAGE", language });
    const response = await dispatchFetch(worker.handlers.fetch, navRequest(`${ORIGIN}/events/new/`));
    expect(await response.text()).toBe(language === "en" ? "English shell" : "German shell");
    await message(worker, { type: "CHECK_PRECACHE" });
    expect(await entries.get("/offline/shell-language/").clone().text()).toBe(language);
    const restarted = workerContext(offlineFetch(), caches);
    expect(await (await dispatchFetch(restarted.handlers.fetch, navRequest(`${ORIGIN}/events/new/`))).text()).toBe(language === "en" ? "English shell" : "German shell");
});
it("missing English shell with stalled repair ends in an English error page", async () => {
    vi.useFakeTimers();
    try {
        const { caches, names } = preferenceCache(new Map([
            ["/offline/shell-language/", new Response("en")],
            ["/offline/event-shell/?shell_language=de", new Response("German shell")],
        ]), { addAll: () => new Promise(() => {}) });
        names.add("anlaufstelle-v64-shell-language-en");
        const worker = workerContext(offlineFetch(), caches);
        const response = dispatchFetch(worker.handlers.fetch, navRequest(`${ORIGIN}/events/new/`));
        await vi.advanceTimersByTimeAsync(3000);
        const resolved = await response;
        expect(resolved.status).toBe(503);
        expect(await resolved.text()).toContain('<html lang="en">');
    } finally { vi.useRealTimers(); }
});

it.each([["de", "en"], ["en", "de"]])("a failed %s to %s preference write cannot revive the previous shell after restart", async (previous, language) => {
    const entries = new Map([
        ["/offline/shell-language/", new Response(previous)],
        ["/offline/event-shell/?shell_language=en", new Response("English shell")],
        ["/offline/event-shell/?shell_language=de", new Response("German shell")],
    ]);
    const { cache, caches } = preferenceCache(entries);
    let worker = workerContext(offlineFetch(), caches);
    await message(worker, { type: "SET_SHELL_LANGUAGE", language: previous });
    cache.put.mockRejectedValue(new Error("QuotaExceededError"));
    await message(worker, { type: "SET_SHELL_LANGUAGE", language });
    const selectedText = language === "en" ? "English shell" : "German shell";
    expect(await (await dispatchFetch(worker.handlers.fetch, navRequest(`${ORIGIN}/events/new/`))).text()).toBe(selectedText);
    worker = workerContext(offlineFetch(), caches);
    const response = await dispatchFetch(worker.handlers.fetch, navRequest(`${ORIGIN}/events/new/`));
    expect(await response.text()).not.toBe(previous === "en" ? "English shell" : "German shell");
});

it("a fresh installation without any language selection serves the default shell", async () => {
    const { caches } = preferenceCache(new Map([
        ["/offline/event-shell/?shell_language=de", new Response("German shell")],
        ["/offline/event-shell/?shell_language=en", new Response("English shell")],
        ["/offline/client-shell/?shell_language=de", new Response("German client shell")],
    ]));
    const worker = workerContext(offlineFetch(), caches);
    const create = await dispatchFetch(worker.handlers.fetch, navRequest(`${ORIGIN}/events/new/`));
    expect(create.status).toBe(200);
    expect(await create.text()).toBe("German shell");
    const client = await dispatchFetch(worker.handlers.fetch, navRequest(`${ORIGIN}/clients/${UUID}/`));
    expect(await client.text()).toBe("German client shell");
});

it("a language selected later replaces the default shell of a fresh installation", async () => {
    const { caches } = preferenceCache(new Map([
        ["/offline/event-shell/?shell_language=de", new Response("German shell")],
        ["/offline/event-shell/?shell_language=en", new Response("English shell")],
    ]));
    let worker = workerContext(offlineFetch(), caches);
    expect(await (await dispatchFetch(worker.handlers.fetch, navRequest(`${ORIGIN}/events/new/`))).text()).toBe("German shell");
    await message(worker, { type: "SET_SHELL_LANGUAGE", language: "en" });
    worker = workerContext(offlineFetch(), caches);
    expect(await (await dispatchFetch(worker.handlers.fetch, navRequest(`${ORIGIN}/events/new/`))).text()).toBe("English shell");
});

it("interrupted language marker cleanup remains uncertain after worker restart", async () => {
    const { caches, names } = preferenceCache(new Map([
        ["/offline/shell-language/", new Response("de")],
        ["/offline/event-shell/?shell_language=de", new Response("German shell")],
        ["/offline/event-shell/?shell_language=en", new Response("English shell")],
    ]));
    let worker = workerContext(offlineFetch(), caches);
    await message(worker, { type: "SET_SHELL_LANGUAGE", language: "de" });
    caches.delete = async () => { throw new Error("Cache deletion failed"); };
    await message(worker, { type: "SET_SHELL_LANGUAGE", language: "en" });
    expect(names.has("anlaufstelle-v64-shell-language-de")).toBe(true);
    expect(names.has("anlaufstelle-v64-shell-language-en")).toBe(true);
    worker = workerContext(offlineFetch(), caches);
    const response = await dispatchFetch(worker.handlers.fetch, navRequest(`${ORIGIN}/events/new/`));
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("German shell");
});

it("failed replacement marker creation invalidates the previous selection", async () => {
    const { caches } = preferenceCache(new Map([
        ["/offline/event-shell/?shell_language=de", new Response("German shell")],
        ["/offline/event-shell/?shell_language=en", new Response("English shell")],
    ]));
    let worker = workerContext(offlineFetch(), caches);
    await message(worker, { type: "SET_SHELL_LANGUAGE", language: "de" });
    const open = caches.open;
    caches.open = async name => {
        if (name === "anlaufstelle-v64-shell-language-en") throw new Error("Marker creation failed");
        return open(name);
    };
    await message(worker, { type: "SET_SHELL_LANGUAGE", language: "en" });
    worker = workerContext(offlineFetch(), caches);
    const response = await dispatchFetch(worker.handlers.fetch, navRequest(`${ORIGIN}/events/new/`));
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("German shell");
});

it("contradictory durable language records cannot authorize the previous shell", async () => {
    const { caches } = preferenceCache(new Map([
        ["/offline/event-shell/?shell_language=de", new Response("German shell")],
        ["/offline/event-shell/?shell_language=en", new Response("English shell")],
    ]));
    let worker = workerContext(offlineFetch(), caches);
    await message(worker, { type: "SET_SHELL_LANGUAGE", language: "de" });
    const open = caches.open;
    caches.open = async name => {
        if (name === "anlaufstelle-v64-shell-language-en") throw new Error("Marker creation failed");
        return open(name);
    };
    caches.delete = async () => { throw new Error("Marker invalidation failed"); };
    await message(worker, { type: "SET_SHELL_LANGUAGE", language: "en" });
    worker = workerContext(offlineFetch(), caches);
    const response = await dispatchFetch(worker.handlers.fetch, navRequest(`${ORIGIN}/events/new/`));
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("German shell");
});

it("acknowledges language persistence only after the durable records agree", async () => {
    const entries = new Map();
    const { cache, caches } = preferenceCache(entries);
    const put = cache.put.getMockImplementation();
    cache.put.mockRejectedValue(new Error("Cache write failed"));
    const worker = workerContext(offlineFetch(), caches);
    const sender = { postMessage: vi.fn() };
    const send = async () => {
        let task;
        worker.handlers.message({ data: { type: "SET_SHELL_LANGUAGE", language: "en", requestId: "current-request" }, source: sender, waitUntil: value => { task = value; } });
        await task;
    };
    await send(); expect(sender.postMessage).not.toHaveBeenCalled();
    cache.put.mockImplementation(put); await send();
    expect(sender.postMessage).toHaveBeenCalledExactlyOnceWith({ type: "SHELL_LANGUAGE_STORED", language: "en", requestId: "current-request" });
});

// If every durable language change fails, consistent old records survive.
// A restarted worker can use the old language until a fresh client message.
it.fails("combined language storage failures cannot revive the old shell after restart", async () => {
    const { cache, caches, names } = preferenceCache(new Map([
        ["/offline/event-shell/?shell_language=de", new Response("German shell")],
        ["/offline/event-shell/?shell_language=en", new Response("English shell")],
    ]));
    let worker = workerContext(offlineFetch(), caches);
    await message(worker, { type: "SET_SHELL_LANGUAGE", language: "de" });
    const open = caches.open;
    caches.open = async name => {
        if (!names.has(name)) throw new Error("Cache creation unavailable");
        return open(name);
    };
    caches.delete = async () => { throw new Error("Cache deletion unavailable"); };
    cache.put.mockRejectedValue(new Error("Cache write unavailable"));
    await message(worker, { type: "SET_SHELL_LANGUAGE", language: "en" });
    expect(await (await dispatchFetch(worker.handlers.fetch, navRequest(`${ORIGIN}/events/new/`))).text()).toBe("English shell");
    worker = workerContext(offlineFetch(), caches);
    const response = await dispatchFetch(worker.handlers.fetch, navRequest(`${ORIGIN}/events/new/`));
    expect(await response.text()).not.toContain("German shell");
});
