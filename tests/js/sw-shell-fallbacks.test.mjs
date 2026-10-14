// Refs #1828: Verhaltens-Pin der Offline-Fallback-Kette und der drei
// Flash-Responses des Service Workers VOR der Konsolidierung auf
// SHELL_ROUTES/_serveShell()/_flashResponse() — echter sw.js-Code im
// VM-Kontext (Muster sw-tally.test.mjs). Die Reihenfolge-Semantik der Kette
// (u.a. WORKITEM_NEW vor WORKITEM_LIST, HX-Partial-Banner VOR den Shells)
// wird hier als beobachtbares Verhalten festgenagelt; die E2E-Suiten
// (test_sw_robustness/test_pwa_offline) laufen erst im Integrationsfenster.
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
    return { handlers, queued };
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

// Cache-Stub: liefert fuer bekannte String-Keys eine markierte Response.
function cachesWith(entries) {
    return {
        match: vi.fn(async (key) => {
            const name = (typeof key === "string" ? key : key.url).replace("?shell_language=de", "");
            const value = entries[name] || entries[new URL(name, ORIGIN).href];
            return value ? new Response("cached:" + value) : undefined;
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
    // This route contract starts after the authenticated page reports DE.
    let languageTask;
    handlers.message({ data: { type: "SET_SHELL_LANGUAGE", language: "de" }, waitUntil: value => { languageTask = value; } });
    await languageTask;
    const response = await dispatchFetch(handlers.fetch, navRequest(url, headers));
    return { status: response.status, body: await response.text(), response };
}

describe("Offline-Fallback-Kette (Navigationen ohne Netz)", () => {
    it("HX-Partial-GET bekommt das kompakte Banner, NIE eine Voll-Shell", async () => {
        const { status, body } = await offlineNavigate(`${ORIGIN}/clients/`, ALL_SHELLS, {
            "HX-Request": "true",
        });
        expect(status).toBe(200);
        expect(body).toContain('data-testid="offline-partial-banner"');
        expect(body).not.toContain("cached:");
    });

    it("Client-Detail wird IN-PLACE mit dem pk-losen Client-Shell beantwortet", async () => {
        const { body } = await offlineNavigate(`${ORIGIN}/clients/${UUID}/`);
        expect(body).toBe("cached:client-shell");
    });

    it("Client-Detail ohne gecachten Shell faellt auf /offline/ zurueck", async () => {
        const entries = { ...ALL_SHELLS };
        delete entries["/offline/client-shell/"];
        const { body } = await offlineNavigate(`${ORIGIN}/clients/${UUID}/`, entries);
        expect(body).toBe("cached:fallback-home");
    });

    it("Konflikt-Review bekommt den Konflikt-Shell", async () => {
        const { body } = await offlineNavigate(`${ORIGIN}/offline/conflicts/${UUID}/`);
        expect(body).toBe("cached:conflict-shell");
    });

    it("/events/new/ bekommt den Event-Create-Shell", async () => {
        const { body } = await offlineNavigate(`${ORIGIN}/events/new/`);
        expect(body).toBe("cached:event-shell");
    });

    it("/workitems/new/ bekommt den WorkItem-Create-Shell (VOR der Listen-Stufe)", async () => {
        const { body } = await offlineNavigate(`${ORIGIN}/workitems/new/`);
        expect(body).toBe("cached:workitem-shell");
    });

    it("/clients/ bekommt den Personenlisten-Shell", async () => {
        const { body } = await offlineNavigate(`${ORIGIN}/clients/`);
        expect(body).toBe("cached:client-list-shell");
    });

    it("/workitems/ bekommt den Aufgaben-Listen-Shell", async () => {
        const { body } = await offlineNavigate(`${ORIGIN}/workitems/`);
        expect(body).toBe("cached:workitem-list-shell");
    });

    it("die Wurzel-URL / bekommt die Zeitstrom-Chronik-Shell", async () => {
        const { body } = await offlineNavigate(`${ORIGIN}/`);
        expect(body).toBe("cached:zeitstrom-shell");
    });

    it("sonstige URLs: erst der URL-genaue Cache-Treffer …", async () => {
        const entries = { ...ALL_SHELLS, [`${ORIGIN}/offline/conflicts/`]: "conflict-list" };
        const { body } = await offlineNavigate(`${ORIGIN}/offline/conflicts/`, entries);
        expect(body).toBe("cached:conflict-list");
    });

    it("… sonst die /offline/-Home", async () => {
        const { body } = await offlineNavigate(`${ORIGIN}/statistics/`);
        expect(body).toBe("cached:fallback-home");
    });
});

describe("Flash-Responses des Schreibpfads", () => {
    it("Multipart-Upload offline -> roter 503-Flash (kein Queueing)", async () => {
        const { handlers, queued } = workerContext(offlineFetch(), cachesWith(ALL_SHELLS));
        const response = await dispatchFetch(
            handlers.fetch,
            new Request(`${ORIGIN}/events/new/`, {
                method: "POST",
                headers: { "Content-Type": "multipart/form-data; boundary=x" },
                body: "--x--",
            })
        );
        expect(response.status).toBe(503);
        expect(response.statusText).toBe("Offline-Upload not supported");
        expect(response.headers.get("HX-Retarget")).toBe("#flash-messages");
        expect(response.headers.get("HX-Reswap")).toBe("outerHTML");
        const body = await response.text();
        expect(body).toContain('id="flash-messages"');
        expect(body).toContain("bg-red-50");
        expect(body).toContain("text-red-800");
        expect(body).toContain("Datei-Uploads erfordern eine Internetverbindung");
        expect(queued).toHaveLength(0);
    });

    it("gequeuter Write -> gelber 200-Flash mit X-Offline-Queued", async () => {
        const { handlers, queued } = workerContext(offlineFetch(), cachesWith(ALL_SHELLS));
        const response = await dispatchFetch(
            handlers.fetch,
            new Request(`${ORIGIN}/events/new/`, {
                method: "POST",
                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                body: "titel=x",
            })
        );
        expect(response.status).toBe(200);
        expect(response.headers.get("X-Offline-Queued")).toBe("1");
        expect(response.headers.get("HX-Retarget")).toBe("#flash-messages");
        expect(response.headers.get("HX-Reswap")).toBe("outerHTML");
        const body = await response.text();
        expect(body).toContain("bg-yellow-50");
        expect(body).toContain("text-yellow-800");
        expect(body).toContain("lokal verschlüsselt");
        expect(queued).toHaveLength(1);
    });

    it("gescheiterte Persistenz -> roter 503-Flash mit Grund", async () => {
        const clients = {
            get: async () => ({
                postMessage(payload, ports) {
                    queueMicrotask(() =>
                        ports[0].peer.onmessage({ data: { type: "QUEUE_NACK", reason: "NoSessionKey" } })
                    );
                },
            }),
            matchAll: async () => [],
            claim: () => {},
        };
        const { handlers } = workerContext(offlineFetch(), cachesWith(ALL_SHELLS), clients);
        const response = await dispatchFetch(
            handlers.fetch,
            new Request(`${ORIGIN}/events/new/`, {
                method: "POST",
                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                body: "titel=x",
            })
        );
        expect(response.status).toBe(503);
        expect(response.statusText).toBe("Offline-Queue persistence failed");
        const body = await response.text();
        expect(body).toContain("bg-red-50");
        expect(body).toContain("text-red-800");
        expect(body).toContain("NoSessionKey");
        expect(response.headers.get("X-Offline-Queued")).toBe(null);
    });
});
