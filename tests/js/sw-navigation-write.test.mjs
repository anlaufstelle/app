// Refs #2730: a native <form method="post"> submitted offline is a navigation.
// The worker must not answer it with the HTMX flash fragment (the browser would
// replace the whole page with a bare fragment: no navigation, no styles, no
// sw-register.js). A navigation gets a redirect to the page it came from; the
// server page or offline shell served for that page shows the notice. HTMX requests and the
// fetch() of the full-page number form keep the fragment with its HTMX headers.
//
// Real sw.js code in a VM context (pattern: sw-tally.test.mjs).
import { readFileSync } from "node:fs";
import vm from "node:vm";

import { describe, expect, it, vi } from "vitest";

const SW_SOURCE = readFileSync("src/static/js/sw.js", "utf8");
const URL_PATTERNS_SOURCE = readFileSync("src/static/js/url-patterns.js", "utf8");
const ORIGIN = "https://example.test";
const FORM_PAGE = `${ORIGIN}/workitems/new/`;
const SHELL_POLICY = "default-src 'self'; base-uri 'none'; form-action 'self'";

function shellHtml(path) {
    return (
        '<!doctype html><html lang="de"><body><nav data-testid="offline-nav">nav</nav>' +
        '<main id="main-content"><div id="flash-messages" role="status" aria-live="polite" aria-atomic="true"></div>' +
        `<p>${path}</p></main><script src="/static/js/sw-register.js" defer></script></body></html>`
    );
}

function cachedShells() {
    return {
        match: vi.fn(async (target) => {
            const path = String(target).split("?")[0];
            if (!path.startsWith("/offline/")) return undefined;
            return new Response(shellHtml(path), {
                status: 200,
                headers: { "Content-Type": "text/html; charset=utf-8", "Content-Security-Policy": SHELL_POLICY },
            });
        }),
        keys: async () => [],
        open: async () => ({ match: async () => undefined, put: async () => {}, keys: async () => [], addAll: async () => {} }),
    };
}

function workerContext({ ack = { type: "QUEUE_ACK" } } = {}) {
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
        crypto: { randomUUID: () => "minted-sw-key" },
        fetch: vi.fn(async () => {
            throw new TypeError("offline");
        }),
        caches: cachedShells(),
        console,
    };
    context.self = context;
    context.location = { origin: ORIGIN };
    context.addEventListener = (type, callback) => {
        handlers[type] = callback;
    };
    // Navigations carry no clientId; the worker asks the first open window.
    const windowClient = {
        postMessage(payload, ports) {
            queued.push(payload);
            queueMicrotask(() => ports[0].peer.onmessage({ data: ack }));
        },
    };
    context.clients = {
        get: async () => windowClient,
        matchAll: async () => [windowClient],
        claim: () => {},
    };
    context.importScripts = () => {
        vm.runInContext(URL_PATTERNS_SOURCE, sandbox);
    };
    const sandbox = vm.createContext(context);
    vm.runInContext(SW_SOURCE, sandbox);
    return { handlers, queued, context };
}

async function selectLanguage(handlers) {
    let task;
    handlers.message({ data: { type: "SET_SHELL_LANGUAGE", language: "de" }, waitUntil: (value) => { task = value; } });
    await task;
}

// Request objects built in Node cannot carry mode "navigate" or a page referrer;
// the worker reads both from the event's request, so they are set on the instance.
function asNavigation(request, referrer = FORM_PAGE) {
    Object.defineProperty(request, "mode", { value: "navigate" });
    Object.defineProperty(request, "referrer", { value: referrer });
    return request;
}

function nativePost(url, { referrer, body = "title=Anruf&csrfmiddlewaretoken=t", contentType } = {}) {
    return asNavigation(
        new Request(url, {
            method: "POST",
            headers: {
                Accept: "text/html,application/xhtml+xml",
                "Content-Type": contentType || "application/x-www-form-urlencoded",
            },
            body,
        }),
        referrer
    );
}

function navigationGet(url) {
    return asNavigation(new Request(url, { headers: { Accept: "text/html,application/xhtml+xml" } }), "");
}

function dispatchFetch(handler, request) {
    let responsePromise = null;
    handler({
        request,
        clientId: "",
        respondWith(value) {
            responsePromise = Promise.resolve(value);
        },
        waitUntil() {},
    });
    return responsePromise;
}

describe("offline native form POST (navigation)", () => {
    it("is queued and answered with a redirect to the form page, not with the HTMX fragment", async () => {
        const { handlers, queued } = workerContext();

        const response = await dispatchFetch(handlers.fetch, nativePost(FORM_PAGE));

        expect(queued).toHaveLength(1);
        expect(response.status).toBe(303);
        expect(response.headers.get("Location")).toBe(FORM_PAGE);
        expect(response.headers.get("HX-Retarget")).toBeNull();
        expect(response.headers.get("X-Offline-Queued")).toBeNull();
        expect(response.headers.get("Content-Security-Policy")).toBe(SHELL_POLICY);
    });

    it("the page reached by the redirect is the full offline shell carrying the notice once", async () => {
        const { handlers } = workerContext();
        await selectLanguage(handlers);
        await dispatchFetch(handlers.fetch, nativePost(FORM_PAGE));

        const page = await dispatchFetch(handlers.fetch, navigationGet(FORM_PAGE));
        const html = await page.text();

        expect(page.status).toBe(200);
        expect(page.headers.get("Content-Security-Policy")).toBe(SHELL_POLICY);
        expect(html).toContain('data-testid="offline-nav"');
        expect(html).toContain("/static/js/sw-register.js");
        expect(html).toMatch(
            /<div id="flash-messages"[^>]*><div [^>]*data-testid="offline-navigation-notice"[^>]*><p [^>]*>Offline — Ihre Eingaben wurden lokal verschlüsselt/
        );

        const again = await (await dispatchFetch(handlers.fetch, navigationGet(FORM_PAGE))).text();
        expect(again).not.toContain("offline-navigation-notice");
    });

    it.each([true, false])("shows each queued POST notice once when the follow-up server is reachable: %s", async (online) => {
        const { handlers, context, queued } = workerContext();
        await selectLanguage(handlers);
        const serverHtml = shellHtml("server form");
        for (let submission = 0; submission < 2; submission += 1) {
            context.fetch.mockRejectedValue(new TypeError("write timed out"));
            await dispatchFetch(handlers.fetch, nativePost(FORM_PAGE));
            if (online) {
                context.fetch.mockImplementation(async () => new Response(serverHtml, {
                    status: 200,
                    headers: {
                        "Content-Type": "text/html; charset=utf-8",
                        "Content-Security-Policy": SHELL_POLICY,
                        "Content-Length": String(serverHtml.length),
                        "X-Server-Page": "yes",
                    },
                }));
            }

            // An intervening HTMX read must neither consume nor acquire the notice.
            const partial = await dispatchFetch(handlers.fetch, new Request(FORM_PAGE, {
                headers: { "HX-Request": "true" },
            }));
            const partialHtml = await partial.text();
            expect(partialHtml).not.toContain("offline-navigation-notice");
            if (online) expect(partialHtml).toBe(serverHtml);
            else expect(partialHtml).toContain("offline-partial-banner");

            const page = await dispatchFetch(handlers.fetch, navigationGet(FORM_PAGE));
            const html = await page.text();
            expect(page.status).toBe(200);
            expect(page.headers.get("Content-Security-Policy")).toBe(SHELL_POLICY);
            expect(page.headers.get("HX-Retarget")).toBeNull();
            expect(html).toMatch(/^<!doctype html>/);
            expect(html.match(/data-testid="offline-navigation-notice"/g)).toHaveLength(1);
            expect(html).toContain("lokal verschlüsselt");
            expect(page.headers.get("Content-Length")).toBeNull();
            if (online) {
                expect(html).toContain("server form");
                expect(page.headers.get("X-Server-Page")).toBe("yes");
            }
            const reload = await dispatchFetch(handlers.fetch, navigationGet(FORM_PAGE));
            expect(await reload.text()).not.toContain("offline-navigation-notice");
            expect(queued).toHaveLength(submission + 1);
        }
    });

    it("the notice is only shown on the page the redirect points to", async () => {
        const { handlers } = workerContext();
        await selectLanguage(handlers);
        await dispatchFetch(handlers.fetch, nativePost(FORM_PAGE));

        const other = await (await dispatchFetch(handlers.fetch, navigationGet(`${ORIGIN}/clients/`))).text();

        expect(other).not.toContain("offline-navigation-notice");
    });

    it.each([
        ["no referrer", ""],
        ["a foreign referrer", "https://elsewhere.example/form/"],
    ])("falls back to the start page for %s", async (_label, referrer) => {
        const { handlers } = workerContext();

        const response = await dispatchFetch(handlers.fetch, nativePost(FORM_PAGE, { referrer }));

        expect(response.status).toBe(303);
        expect(response.headers.get("Location")).toBe(`${ORIGIN}/`);
    });

    it("a failed persist redirects as well and the shell shows the red notice", async () => {
        const { handlers } = workerContext({ ack: { type: "QUEUE_NACK", reason: "NoSessionKey" } });
        await selectLanguage(handlers);

        const response = await dispatchFetch(handlers.fetch, nativePost(FORM_PAGE));
        expect(response.status).toBe(303);

        const html = await (await dispatchFetch(handlers.fetch, navigationGet(FORM_PAGE))).text();
        expect(html).toContain("bg-red-50");
        expect(html).toContain("NoSessionKey");
    });

    it("an offline multipart upload redirects as well and the shell shows the upload notice", async () => {
        const { handlers } = workerContext();
        await selectLanguage(handlers);

        const response = await dispatchFetch(
            handlers.fetch,
            nativePost(FORM_PAGE, { contentType: "multipart/form-data; boundary=x", body: "--x--" })
        );
        expect(response.status).toBe(303);

        const html = await (await dispatchFetch(handlers.fetch, navigationGet(FORM_PAGE))).text();
        expect(html).toContain("Datei-Uploads erfordern eine Internetverbindung");
    });

    it("escapes markup in the notice text", async () => {
        const { handlers } = workerContext({ ack: { type: "QUEUE_NACK", reason: "<img src=x>" } });
        await selectLanguage(handlers);
        await dispatchFetch(handlers.fetch, nativePost(FORM_PAGE));

        const html = await (await dispatchFetch(handlers.fetch, navigationGet(FORM_PAGE))).text();

        expect(html).not.toContain("<img src=x>");
        expect(html).toContain("&lt;img src=x&gt;");
    });
});

describe("offline POSTs that are not navigations keep the fragment", () => {
    it("an HTMX POST gets the flash fragment with the HTMX headers", async () => {
        const { handlers } = workerContext();
        const request = new Request(FORM_PAGE, {
            method: "POST",
            headers: { "HX-Request": "true", "Content-Type": "application/x-www-form-urlencoded" },
            body: "title=Anruf",
        });

        const response = await dispatchFetch(handlers.fetch, request);

        expect(response.status).toBe(200);
        expect(response.headers.get("HX-Retarget")).toBe("#flash-messages");
        expect(response.headers.get("X-Offline-Queued")).toBe("1");
        expect(await response.text()).toMatch(/^<div id="flash-messages">/);
    });

    it("the fetch() of the full-page number form (Accept: text/html, no HX-Request) gets the fragment", async () => {
        const { handlers } = workerContext();
        const request = new Request(`${ORIGIN}/partials/tally/increment/`, {
            method: "POST",
            headers: { Accept: "text/html", "Content-Type": "application/x-www-form-urlencoded" },
            body: "document_type=abc&idempotency_key=body-key-123&anzahl=7",
        });

        const response = await dispatchFetch(handlers.fetch, request);

        expect(response.status).toBe(200);
        expect(response.headers.get("HX-Retarget")).toBe("#flash-messages");
        expect(response.headers.get("X-Offline-Queued")).toBe("1");
    });
});
