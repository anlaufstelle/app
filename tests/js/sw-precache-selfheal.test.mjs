// Refs #1948: Precache-Selbstheilung. Raeumt der Browser unter Speicherdruck
// die Cache Storage teilweise ab (auf iOS realistisch), liefen bisher weder
// install noch activate je wieder — das Geraet verlor still seine
// Offline-Faehigkeit bis zum naechsten CACHE_NAME-Bump. Der SW prueft jetzt
// auf ein CHECK_PRECACHE-Message (sw-register.js schickt es einmal pro
// Seitenladen) jeden APP_SHELL-Eintrag per URL-genauem cache.match und laedt
// NUR die fehlenden nach.
//
// Echter sw.js-Code im VM-Kontext (Muster sw-tally.test.mjs /
// sw-shell-fallbacks.test.mjs).
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
    return { handlers, sandbox };
}

// Cache-Stub: `present` = Menge der URLs, fuer die match() trifft.
function cacheStub(present) {
    const cache = {
        match: vi.fn(async (url) => (present.has(url) ? new Response("hit") : undefined)),
        addAll: vi.fn(async (urls) => {
            urls.forEach((u) => present.add(u));
        }),
        keys: async () => [],
        put: async () => {},
        delete: async () => true,
    };
    return {
        cache,
        caches: {
            open: vi.fn(async () => cache),
            match: async () => undefined,
            keys: async () => [],
            delete: async () => true,
        },
    };
}

/** Das APP_SHELL-Array aus der ausgefuehrten sw.js-Quelle ziehen. */
function appShellOf(sandbox) {
    return vm.runInContext("APP_SHELL", sandbox);
}

async function dispatchMessage(handlers, data) {
    const waited = [];
    handlers.message({
        data,
        waitUntil: (p) => {
            waited.push(Promise.resolve(p));
        },
    });
    await Promise.all(waited);
    return waited.length;
}

describe("#1948 — CHECK_PRECACHE heilt einen unvollstaendigen Precache", () => {
    it("vollstaendiger Precache: prueft alle Eintraege, laedt NICHTS nach", async () => {
        const present = new Set();
        const stub = cacheStub(present);
        const { handlers, sandbox } = workerContext(stub.caches);
        appShellOf(sandbox).forEach((u) => present.add(u));

        await dispatchMessage(handlers, { type: "CHECK_PRECACHE" });

        expect(stub.cache.match).toHaveBeenCalledTimes(appShellOf(sandbox).length);
        expect(stub.cache.addAll).not.toHaveBeenCalled();
    });

    it("teilgeleerter Precache: laedt GENAU die fehlenden Eintraege nach", async () => {
        const present = new Set();
        const stub = cacheStub(present);
        const { handlers, sandbox } = workerContext(stub.caches);
        const shell = appShellOf(sandbox);
        const missing = [shell[0], shell[3], shell[shell.length - 1]];
        shell.filter((u) => !missing.includes(u)).forEach((u) => present.add(u));

        await dispatchMessage(handlers, { type: "CHECK_PRECACHE" });

        expect(stub.cache.addAll).toHaveBeenCalledTimes(1);
        expect(stub.cache.addAll).toHaveBeenCalledWith(missing);
    });

    it("Nachladen scheitert (offline): Fehler wird still geschluckt", async () => {
        const present = new Set();
        const stub = cacheStub(present);
        stub.cache.addAll.mockRejectedValue(new TypeError("offline"));
        const { handlers } = workerContext(stub.caches);

        // Darf nicht rejecten — die naechste Navigation versucht es erneut.
        await expect(
            (async () => {
                await dispatchMessage(handlers, { type: "CHECK_PRECACHE" });
            })()
        ).resolves.toBeUndefined();
    });

    it("laeuft unter waitUntil (der SW darf waehrend der Heilung nicht entladen)", async () => {
        const present = new Set();
        const stub = cacheStub(present);
        const { handlers } = workerContext(stub.caches);

        const waited = await dispatchMessage(handlers, { type: "CHECK_PRECACHE" });

        expect(waited).toBeGreaterThan(0);
    });

    it("SKIP_WAITING bleibt unberuehrt und loest keine Heilung aus", async () => {
        const present = new Set();
        const stub = cacheStub(present);
        const { handlers, sandbox } = workerContext(stub.caches);

        await dispatchMessage(handlers, { type: "SKIP_WAITING" });

        expect(sandbox.skipWaiting).toHaveBeenCalledTimes(1);
        expect(stub.cache.match).not.toHaveBeenCalled();
        expect(stub.cache.addAll).not.toHaveBeenCalled();
    });
});
