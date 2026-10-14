// #1912: echter Service-Worker-Code im VM-Kontext. So werden Same-Origin-
// Allowlist und Body-Key-Transport getestet, ohne die Produktquelle fuer Tests
// um Exporte zu erweitern.
import { readFileSync } from "node:fs";
import vm from "node:vm";

import { beforeEach, describe, expect, it, vi } from "vitest";

const SW_SOURCE = readFileSync("src/static/js/sw.js", "utf8");
const URL_PATTERNS_SOURCE = readFileSync("src/static/js/url-patterns.js", "utf8");
const ORIGIN = "https://example.test";

function workerContext(fetchImpl, cachesImpl = {}) {
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
    context.clients = {
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
    return { sandbox, handlers, queued };
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

describe("Service-Worker Tally-Allowlist", () => {
    let fetchMock;

    beforeEach(() => {
        fetchMock = vi.fn(async () => new Response("ok", { status: 200 }));
    });

    it("interceptet den exakten same-origin POST", async () => {
        const { handlers } = workerContext(fetchMock);
        const response = dispatchFetch(
            handlers.fetch,
            new Request(`${ORIGIN}/partials/tally/increment/`, { method: "POST", body: "document_type=1" })
        );
        expect(response).not.toBeNull();
        await response;
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it.each([
        "https://evil.example/partials/tally/increment/",
        `${ORIGIN}/partials/tally/increment/extra/`,
        `${ORIGIN}/partials/tally/increment-evil/`,
    ])("interceptet %s nicht", (url) => {
        const { handlers } = workerContext(fetchMock);
        const response = dispatchFetch(handlers.fetch, new Request(url, { method: "POST", body: "x=1" }));
        expect(response).toBeNull();
        expect(fetchMock).not.toHaveBeenCalled();
    });
});

describe("Service-Worker Body-Idempotenz-Key", () => {
    it("nutzt einen validen URL-encoded Body-Key fuer Erstversuch und Queue", async () => {
        let attempted;
        const fetchMock = vi.fn(async (request) => {
            attempted = request;
            throw new TypeError("offline");
        });
        const { handlers, queued, sandbox } = workerContext(fetchMock);
        const body = "document_type=abc&idempotency_key=body-key-123&anzahl=7";
        const request = new Request(`${ORIGIN}/partials/tally/increment/`, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body,
        });

        const response = await dispatchFetch(handlers.fetch, request);

        expect(response.status).toBe(200);
        expect(response.headers.get("X-Offline-Queued")).toBe("1");
        expect(attempted.headers.get("X-Idempotency-Key")).toBe("body-key-123");
        expect(queued).toHaveLength(1);
        expect(queued[0].body).toBe(body);
        expect(queued[0].headers["x-idempotency-key"]).toBe("body-key-123");
        expect(sandbox.crypto.randomUUID).not.toHaveBeenCalled();
    });

    it("mintet ohne Header und ohne validen Body-Key genau einen gemeinsamen Key", async () => {
        let attempted;
        const fetchMock = vi.fn(async (request) => {
            attempted = request;
            throw new TypeError("offline");
        });
        const { handlers, queued, sandbox } = workerContext(fetchMock);
        const request = new Request(`${ORIGIN}/partials/tally/increment/`, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: "document_type=abc",
        });

        await dispatchFetch(handlers.fetch, request);

        expect(sandbox.crypto.randomUUID).toHaveBeenCalledTimes(1);
        expect(attempted.headers.get("X-Idempotency-Key")).toBe("minted-sw-key");
        expect(queued[0].headers["x-idempotency-key"]).toBe("minted-sw-key");
    });
});

describe("Service-Worker Offline-Create-Shells nach Path-Anchoring", () => {
    it.each([
        ["/events/new/", "/offline/event-shell/?shell_language=de"],
        ["/workitems/new/", "/offline/workitem-shell/?shell_language=de"],
    ])("serves the selected language variant for %s at %s", async (path, shellPath) => {
        const fetchMock = vi.fn(async () => {
            throw new TypeError("offline");
        });
        const cachesMock = {
            match: vi.fn(async (target) => new Response(String(target), { status: 200 })),
        };
        const { handlers } = workerContext(fetchMock, cachesMock);
        let languageTask;
        handlers.message({ data: { type: "SET_SHELL_LANGUAGE", language: "de" }, waitUntil: value => { languageTask = value; } });
        await languageTask;
        const request = new Request(`${ORIGIN}${path}`, {
            headers: { Accept: "text/html" },
        });

        const response = await dispatchFetch(handlers.fetch, request);

        expect(await response.text()).toBe(shellPath);
        expect(cachesMock.match).toHaveBeenCalledWith(shellPath);
    });
});
