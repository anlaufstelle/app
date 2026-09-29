// Refs #1904: responses the service worker builds itself never pass through
// Django, so they get no Content-Security-Policy from the server chain. They
// adopt the policy of the precached offline home (/offline/) — a response
// the server built from the one policy source — instead of a copy kept in
// sw.js. Without that source in the cache they fall back to a strict minimal
// policy, never to none.
//
// Real sw.js code in a VM context (pattern: sw-shell-fallbacks.test.mjs).
import { readFileSync } from "node:fs";
import vm from "node:vm";

import { describe, expect, it, vi } from "vitest";

const SW_SOURCE = readFileSync("src/static/js/sw.js", "utf8");
const URL_PATTERNS_SOURCE = readFileSync("src/static/js/url-patterns.js", "utf8");
const ORIGIN = "https://example.test";
const OFFLINE_HOME = "/offline/";

// Stands for whatever the server delivered with the cached shell; the test
// only needs a value that cannot come from anywhere else.
const SERVER_POLICY = "default-src 'self'; base-uri 'none'; form-action 'self'; img-src 'self' data: probe.example";
const SERVER_REPORT_ONLY = "default-src 'self'; report-uri /probe-report/";

function workerContext(cachesImpl) {
    const handlers = {};
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
        crypto: { randomUUID: () => "minted-sw-key" },
        fetch: vi.fn(async () => Promise.reject(new TypeError("offline"))),
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
            postMessage(_payload, ports) {
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
    return handlers;
}

function cachesWithShell(headers) {
    return {
        match: vi.fn(async (key) => {
            const url = typeof key === "string" ? key : key.url;
            if (headers && url === OFFLINE_HOME) {
                return new Response("<html>offline home</html>", { headers });
            }
            return undefined;
        }),
        open: vi.fn(async () => ({ keys: async () => [], put: async () => {}, delete: async () => true })),
        keys: async () => [],
        delete: async () => true,
    };
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

const htmxRequest = () =>
    new Request(`${ORIGIN}/clients/?q=a`, { headers: { Accept: "text/html", "HX-Request": "true" } });

const uploadRequest = () =>
    new Request(`${ORIGIN}/events/new/`, {
        method: "POST",
        body: "--x--",
        headers: { "Content-Type": "multipart/form-data; boundary=x", "HX-Request": "true" },
    });

const queuedWriteRequest = () =>
    new Request(`${ORIGIN}/events/new/`, {
        method: "POST",
        body: "name=a",
        headers: { "Content-Type": "application/x-www-form-urlencoded", "HX-Request": "true" },
    });

function directives(policy) {
    return (policy || "")
        .split(";")
        .map((part) => part.trim().split(/\s+/))
        .filter((tokens) => tokens[0])
        .map((tokens) => tokens.join(" "));
}

describe("synthetic responses adopt the server policy of the cached shell", () => {
    it("offline HTMX banner carries the policy of the cached offline home", async () => {
        const handlers = workerContext(cachesWithShell({ "Content-Security-Policy": SERVER_POLICY }));
        const response = await respond(handlers, htmxRequest());
        expect(response.status).toBe(200);
        expect(await response.text()).toContain('data-testid="offline-partial-banner"');
        expect(response.headers.get("Content-Security-Policy")).toBe(SERVER_POLICY);
        expect(response.headers.get("Content-Type")).toBe("text/html; charset=utf-8");
    });

    it("a report-only policy of the shell is carried along", async () => {
        const handlers = workerContext(
            cachesWithShell({
                "Content-Security-Policy": SERVER_POLICY,
                "Content-Security-Policy-Report-Only": SERVER_REPORT_ONLY,
            })
        );
        const response = await respond(handlers, htmxRequest());
        expect(response.headers.get("Content-Security-Policy-Report-Only")).toBe(SERVER_REPORT_ONLY);
    });

    it.each([
        ["offline upload refusal (503)", uploadRequest, 503],
        ["queued write confirmation (200)", queuedWriteRequest, 200],
    ])("write-path flash fragment: %s", async (_label, makeRequest, status) => {
        const handlers = workerContext(cachesWithShell({ "Content-Security-Policy": SERVER_POLICY }));
        const response = await respond(handlers, makeRequest());
        expect(response.status).toBe(status);
        expect(await response.text()).toContain('id="flash-messages"');
        expect(response.headers.get("Content-Security-Policy")).toBe(SERVER_POLICY);
        expect(response.headers.get("HX-Retarget")).toBe("#flash-messages");
    });
});

describe("without the source in the cache the policy falls back, never to none", () => {
    const fallbacks = [
        ["shell missing", () => cachesWithShell(null)],
        ["shell without policy header", () => cachesWithShell({ "Content-Type": "text/html" })],
        [
            "cache lookup fails",
            () => ({ ...cachesWithShell(null), match: vi.fn(async () => Promise.reject(new Error("broken"))) }),
        ],
    ];

    it.each(fallbacks)("HTMX banner: %s", async (_label, makeCaches) => {
        const handlers = workerContext(makeCaches());
        const response = await respond(handlers, htmxRequest());
        expect(response.status).toBe(200);
        const policy = directives(response.headers.get("Content-Security-Policy"));
        expect(policy).toContain("default-src 'none'");
        expect(policy).toContain("base-uri 'none'");
        expect(policy).toContain("form-action 'none'");
        expect(response.headers.get("Content-Security-Policy-Report-Only")).toBeNull();
    });

    it.each(fallbacks)("flash fragment: %s", async (_label, makeCaches) => {
        const handlers = workerContext(makeCaches());
        const response = await respond(handlers, uploadRequest());
        expect(response.status).toBe(503);
        expect(directives(response.headers.get("Content-Security-Policy"))).toContain("default-src 'none'");
    });
});

describe("sw.js keeps no copy of the server policy", () => {
    it("no directive value of the server policy is spelled out in the worker", () => {
        // The server policy allows 'self' sources and reports to /csp-report/;
        // the worker's only literal policy is the strict fallback.
        expect(SW_SOURCE).not.toMatch(/form-action 'self'/);
        expect(SW_SOURCE).not.toMatch(/default-src 'self'/);
        expect(SW_SOURCE).not.toContain("/csp-report/");
    });
});
