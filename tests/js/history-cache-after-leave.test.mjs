// Refs #2660: after logout or "quick exit" the old document must not write the
// htmx history cache (``sessionStorage["htmx-history-cache"]``) or the
// ``filters:<path>`` keys again. Locking htmx requests and responses is not
// enough: the vendored htmx also saves a snapshot from its own ``popstate``
// handler (no request, no ``htmx:beforeOnLoad``), and the request lock of the
// logout path ends 10 s after the submit.
//
// These tests run the real vendored ``htmx.min.js`` (only XMLHttpRequest is
// replaced by a hand-driven stub) together with the real ``sw-register.js``,
// ``quick-exit.js`` and ``filter-persistence.js``, each page in its own jsdom
// window. A re-implementation of htmx in the test could not tell whether the
// vendor code writes the cache.
import { readFileSync } from "node:fs";
import { JSDOM, VirtualConsole } from "jsdom";
import { afterEach, describe, expect, it } from "vitest";

const source = (name) => readFileSync(`src/static/js/${name}.js`, "utf8");
const CACHE_KEY = "htmx-history-cache";
const GRACE_MS = 10000;

// Hand-driven XMLHttpRequest: records the request, answers when the test says so.
class FakeXhr {
    static instances = [];

    constructor() {
        this.upload = { addEventListener() {} };
        this.headers = {};
        this.status = 0;
        this.response = "";
        this.responseText = "";
        this.responseURL = "";
        FakeXhr.instances.push(this);
    }

    open(method, url) {
        this.method = method;
        this.url = url;
        this.responseURL = new URL(url, "https://example.test/").href;
    }

    overrideMimeType() {}

    setRequestHeader(name, value) {
        this.headers[name] = value;
    }

    send() {
        this.sent = true;
    }

    abort() {
        if (this.onabort) this.onabort();
    }

    getResponseHeader(name) {
        const key = Object.keys(this.responseHeaders || {}).find((k) => k.toLowerCase() === name.toLowerCase());
        return key ? this.responseHeaders[key] : null;
    }

    getAllResponseHeaders() {
        return Object.entries(this.responseHeaders || {})
            .map(([k, v]) => `${k}: ${v}`)
            .join("\r\n");
    }

    respond(status, headers, body) {
        this.status = status;
        this.responseHeaders = headers;
        this.response = body;
        this.responseText = body;
        this.onload.call(this);
    }
}

const PAGE = `
    <form id="filters" data-filter-persist>
        <select name="status"><option value="">all</option><option value="open">open</option><option value="done">done</option></select>
        <input name="owner" type="text" value="">
    </form>
    <div id="main"><button id="more" hx-get="/clients/more/" hx-target="#main">more</button></div>
    <form id="logout-form" action="/logout/" method="post"><button type="submit">Logout</button></form>
`;

const openWindows = [];

async function openPage({ scripts = ["htmx.min", "sw-register", "filter-persistence"] } = {}) {
    FakeXhr.instances = [];
    const dom = new JSDOM(`<!doctype html><html><body>${PAGE}</body></html>`, {
        url: "https://example.test/clients/",
        runScripts: "outside-only",
        virtualConsole: new VirtualConsole(), // swallow "navigation not implemented"
    });
    const w = dom.window;
    openWindows.push(w);
    if (w.document.readyState !== "complete") {
        await new Promise((resolve) => w.addEventListener("load", resolve));
    }
    // jsdom needs an explicit XPath result type, which browsers default.
    const evaluate = w.XPathExpression.prototype.evaluate;
    w.XPathExpression.prototype.evaluate = function (node, type = 0, result = null) {
        return evaluate.call(this, node, type, result);
    };
    w.XMLHttpRequest = FakeXhr;
    for (const name of scripts) w.eval(source(name));
    // The page is already loaded: run the DOMContentLoaded initialisers by hand.
    w.document.dispatchEvent(new w.Event("DOMContentLoaded"));
    w.document.getElementById("logout-form").submit = () => {
        w.__logoutSubmits = (w.__logoutSubmits || 0) + 1;
    };
    return w;
}

afterEach(() => {
    openWindows.splice(0).forEach((w) => w.close());
});

// Click the htmx button and answer with a pushed URL: htmx saves the page it
// is leaving to the history cache before it swaps.
async function pushViaHtmx(w) {
    const before = FakeXhr.instances.length;
    w.document.getElementById("more").click();
    const xhr = FakeXhr.instances[before];
    if (!xhr) return null;
    xhr.respond(200, { "HX-Push-Url": "/clients/?page=2", "Content-Type": "text/html" }, "<button id='more' hx-get='/clients/more/' hx-target='#main'>more</button>");
    // Let htmx settle (default 20 ms): until then the new button still carries
    // the request class of the old one and ignores clicks.
    await new Promise((resolve) => w.setTimeout(resolve, 40));
    return xhr;
}

function startPendingHtmxRequest(w) {
    const before = FakeXhr.instances.length;
    w.document.getElementById("more").click();
    return FakeXhr.instances[before];
}

function cache(w) {
    return w.sessionStorage.getItem(CACHE_KEY);
}

function clickLogout(w) {
    const form = w.document.getElementById("logout-form");
    const ev = new w.Event("submit", { bubbles: true, cancelable: true });
    form.dispatchEvent(ev);
    return ev;
}

async function logout(w) {
    const submitsBefore = w.__logoutSubmits || 0;
    clickLogout(w);
    await new Promise((resolve) => {
        const poll = () => ((w.__logoutSubmits || 0) > submitsBefore ? resolve() : w.setTimeout(poll, 1));
        poll();
    });
}

function popBack(w) {
    w.dispatchEvent(new w.PopStateEvent("popstate", { state: { htmx: true } }));
}

function advancePastGraceWindow(w) {
    const later = Date.now() + GRACE_MS + 1;
    w.Date.now = () => later;
}

describe("real htmx writes the history cache (control)", () => {
    it("saves a snapshot when a response pushes a URL and no logout was clicked", async () => {
        const w = await openPage();

        await pushViaHtmx(w);

        expect(cache(w)).not.toBeNull();
    });

    it("keeps the default history cache size without a logout", async () => {
        const w = await openPage();

        expect(w.htmx.config.historyCacheSize).toBe(10);
    });
});

describe("logout: history cache stays empty for the rest of the document", () => {
    it("a pushed response arriving after the logout click writes no snapshot", async () => {
        const w = await openPage();
        const pending = startPendingHtmxRequest(w);
        await logout(w);

        pending.respond(200, { "HX-Push-Url": "/clients/?page=2" }, "<p>late</p>");

        expect(cache(w)).toBeNull();
    });

    it("popstate after the logout click writes no snapshot", async () => {
        const w = await openPage();
        await pushViaHtmx(w);
        expect(cache(w)).not.toBeNull();
        await logout(w);
        expect(cache(w)).toBeNull();

        popBack(w);

        expect(cache(w)).toBeNull();
    });

    it("stays empty after the 10 s request lock has expired", async () => {
        const w = await openPage();
        await pushViaHtmx(w);
        await logout(w);
        advancePastGraceWindow(w);

        popBack(w);
        expect(cache(w)).toBeNull();

        // The request lock is open again; a pushed response must still not save.
        const pending = startPendingHtmxRequest(w);
        expect(pending).toBeTruthy();
        pending.respond(200, { "HX-Push-Url": "/clients/?page=3" }, "<p>later</p>");
        expect(cache(w)).toBeNull();
    });

    it("leaves history caching off only for this document, not for htmx in general", async () => {
        const w = await openPage();
        await logout(w);

        expect(w.htmx.config.historyCacheSize).toBe(0);
        expect(w.htmx.config.historyEnabled).toBe(true);
    });

    it("still submits the logout when htmx is missing", async () => {
        const w = await openPage({ scripts: ["sw-register"] });

        await logout(w);

        expect(w.__logoutSubmits).toBe(1);
    });
});

describe("quick exit: history cache stays empty for the rest of the document", () => {
    it("popstate after the exit writes no snapshot", async () => {
        const w = await openPage({ scripts: ["htmx.min", "quick-exit", "filter-persistence"] });
        await pushViaHtmx(w);
        expect(cache(w)).not.toBeNull();

        await w.quickExit.trigger();
        expect(cache(w)).toBeNull();

        popBack(w);

        expect(cache(w)).toBeNull();
    });
});

describe("filter keys are not written again after the storage was wiped", () => {
    function choose(w, value) {
        const select = w.document.querySelector("select[name=status]");
        select.value = value;
        select.dispatchEvent(new w.Event("change", { bubbles: true }));
    }

    it("saves a filter before leaving (control)", async () => {
        const w = await openPage();

        choose(w, "open");

        expect(w.sessionStorage.getItem("filters:/clients/")).not.toBeNull();
    });

    it("does not store a changed select after the logout click", async () => {
        const w = await openPage();
        choose(w, "open");
        await logout(w);
        expect(w.sessionStorage.getItem("filters:/clients/")).toBeNull();

        choose(w, "done");

        expect(w.sessionStorage.getItem("filters:/clients/")).toBeNull();
    });

    it("does not store typed input after the logout click", async () => {
        const w = await openPage();
        await logout(w);

        const input = w.document.querySelector("input[name=owner]");
        input.value = "pseudonym";
        input.dispatchEvent(new w.Event("input", { bubbles: true }));
        await new Promise((resolve) => w.setTimeout(resolve, 400));

        expect(w.sessionStorage.getItem("filters:/clients/")).toBeNull();
    });

    it("does not store a changed select after quick exit", async () => {
        const w = await openPage({ scripts: ["htmx.min", "quick-exit", "filter-persistence"] });
        choose(w, "open");
        await w.quickExit.trigger();
        expect(w.sessionStorage.getItem("filters:/clients/")).toBeNull();

        choose(w, "done");

        expect(w.sessionStorage.getItem("filters:/clients/")).toBeNull();
    });
});

describe("repeated logout click", () => {
    it("is swallowed while the first logout is fresh", async () => {
        const w = await openPage();
        await logout(w);

        const ev = clickLogout(w);
        await new Promise((resolve) => w.setTimeout(resolve, 20));

        expect(ev.defaultPrevented).toBe(true);
        expect(w.__logoutSubmits).toBe(1);
    });

    it("starts the logout again once the first one did not navigate within the window", async () => {
        const w = await openPage();
        await logout(w);
        advancePastGraceWindow(w);

        await logout(w);

        expect(w.__logoutSubmits).toBe(2);
    });
});

describe("back button with an empty history cache (cache miss)", () => {
    // Without a cached snapshot htmx fetches the page with an XHR of its own,
    // which does not pass through ``htmx:beforeRequest`` / ``htmx:beforeOnLoad``.
    // Answering it must not bring the cache or a filter key back.
    async function backWithCacheMiss(w) {
        popBack(w);
        const xhr = FakeXhr.instances.at(-1);
        expect(xhr.headers["HX-History-Restore-Request"]).toBe("true");
        xhr.respond(200, {}, "<p>x</p>");
    }

    it("writes neither the cache nor a filter key after the logout click", async () => {
        const w = await openPage();
        await pushViaHtmx(w);
        await logout(w);

        await backWithCacheMiss(w);

        expect(cache(w)).toBeNull();
        expect(Object.keys(w.sessionStorage).filter((key) => key.startsWith("filters:"))).toEqual([]);
    });

    it("writes neither the cache nor a filter key after quick exit", async () => {
        const w = await openPage({ scripts: ["htmx.min", "quick-exit", "filter-persistence"] });
        await pushViaHtmx(w);
        await w.quickExit.trigger();

        await backWithCacheMiss(w);

        expect(cache(w)).toBeNull();
        expect(Object.keys(w.sessionStorage).filter((key) => key.startsWith("filters:"))).toEqual([]);
    });
});

describe("path htmx remembers for its history is not left behind after the exit", () => {
    const PATH_KEY = "htmx-current-path-for-history";
    const path = (w) => w.sessionStorage.getItem(PATH_KEY);

    it("keeps the current path while nothing was left (control)", async () => {
        const w = await openPage();

        await pushViaHtmx(w);

        expect(path(w)).toBe("/clients/?page=2");
    });

    it("is removed after a pushed response that arrives once the 10 s request lock expired", async () => {
        const w = await openPage();
        await logout(w);
        advancePastGraceWindow(w);

        const pending = startPendingHtmxRequest(w);
        pending.respond(200, { "HX-Push-Url": "/clients/?q=secret" }, "<p>later</p>");

        expect(path(w)).toBeNull();
    });

    it("is removed after a replaced URL that arrives once the 10 s request lock expired", async () => {
        const w = await openPage();
        await logout(w);
        advancePastGraceWindow(w);

        const pending = startPendingHtmxRequest(w);
        pending.respond(200, { "HX-Replace-Url": "/clients/?q=secret" }, "<p>later</p>");

        expect(path(w)).toBeNull();
    });

    it("is removed after the back button restored a page from the server (logout)", async () => {
        const w = await openPage();
        await pushViaHtmx(w);
        await logout(w);

        popBack(w);
        FakeXhr.instances.at(-1).respond(200, {}, "<p>x</p>");

        expect(path(w)).toBeNull();
    });

    it("is removed after the back button restored a page from the server (quick exit)", async () => {
        const w = await openPage({ scripts: ["htmx.min", "quick-exit", "filter-persistence"] });
        await pushViaHtmx(w);
        await w.quickExit.trigger();

        popBack(w);
        FakeXhr.instances.at(-1).respond(200, {}, "<p>x</p>");

        expect(path(w)).toBeNull();
    });

    it("lets htmx carry on with the key missing", async () => {
        const w = await openPage();
        await pushViaHtmx(w);
        await logout(w);
        const savedPaths = [];
        w.document.body.addEventListener("htmx:beforeHistorySave", (event) => savedPaths.push(event.detail.path));

        popBack(w);
        FakeXhr.instances.at(-1).respond(200, {}, "<p>x</p>");
        popBack(w);

        // The reader falls back to the location when the key is gone.
        expect(savedPaths).toEqual(["/clients/?page=2", "/clients/?page=2"]);
        expect(w.history.state).toEqual({ htmx: true });
        expect(cache(w)).toBeNull();
    });
});
