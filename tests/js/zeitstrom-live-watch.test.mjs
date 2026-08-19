import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

beforeAll(async () => {
    window.__ZEITSTROM_WATCH_TEST__ = true;
    await import("../../src/static/js/zeitstrom-filter-state.js");
    await import("../../src/static/js/zeitstrom-live-watch.js");
});

function response({ status = 200, json = null, text = "", headers = {}, redirected = false, url = "" }) {
    const normalizedHeaders = new Map(
        Object.entries(headers).map(([name, value]) => [name.toLowerCase(), value]),
    );
    return {
        status,
        ok: status >= 200 && status < 300,
        redirected,
        url,
        headers: { get: (name) => normalizedHeaders.get(name.toLowerCase()) || null },
        json: vi.fn(async () => json),
        text: vi.fn(async () => text),
    };
}

function watchResponse(components = [], validators = {}) {
    return response({
        json: { components, validators },
        headers: { "Content-Type": "application/json", ETag: '"page-v1"' },
    });
}

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((yes, no) => {
        resolve = yes;
        reject = no;
    });
    return { promise, resolve, reject };
}

function renderDom() {
    document.body.innerHTML = `
        <main id="zeitstrom-live-watch"
              data-page-scope="opaque-page-scope"
              data-tally-topology="&quot;topology-v1&quot;"
              data-watch-url="/api/v1/zeitstrom/watch/"
              data-feed-url="/partials/zeitstrom/feed/"
              data-tally-counts-url="/partials/zeitstrom/tally-counts/"
              data-current-shift-url="/partials/zeitstrom/current-shift/"
              data-focus-box-url="/partials/zeitstrom/focus-box/">
            <form id="zeitstrom-filter-state">
                <input name="date" value="2026-08-16">
                <input name="time_filter" value="all">
                <input name="type" value="">
                <input name="doc_type" value="">
                <input name="view" value="">
            </form>
            <div id="zeitstrom-bans-slot"></div>
            <div id="zeitstrom-current-shift-slot"></div>
            <div id="tally-panel-slot">
                <div data-testid="tally-row-type-1">
                    <span id="tally-count-type-1" data-testid="tally-count-type-1"
                          data-confirmed-count="2" aria-label="2 Kontakte für Kontakt">2</span>
                    <form data-offline-tally-number-form><input name="menge" value="7"></form>
                </div>
            </div>
            <div id="feed-list"><p>alter Feed</p></div>
            <aside id="zeitstrom-focus-box-slot"></aside>
        </main>`;
    return document.getElementById("zeitstrom-live-watch");
}

function configureTimeFilter(value, tab) {
    tab = tab || document.createElement("button");
    tab.className = "time-filter-tab";
    tab.dataset.zeitstromTimeFilter = value;
    if (!tab.isConnected) document.getElementById("zeitstrom-live-watch").append(tab);
    const requestConfig = {
        elt: tab,
        headers: {},
        parameters: Object.fromEntries(
            ["date", "time_filter", "type", "doc_type", "view"].map((name) => [
                name,
                document.querySelector(`[name="${name}"]`).value,
            ]),
        ),
    };
    tab.dispatchEvent(
        new CustomEvent("htmx:configRequest", { bubbles: true, detail: requestConfig }),
    );
    return { tab, requestConfig };
}

function finishTimeFilterRequest(
    transition,
    { etag = null, validators = null, status = 200, successful = true } = {},
) {
    const responseHeaders = new Map();
    if (etag) responseHeaders.set("etag", etag);
    if (validators) {
        responseHeaders.set("x-zeitstrom-validators", JSON.stringify(validators));
    }
    transition.tab.dispatchEvent(
        new CustomEvent("htmx:afterRequest", {
            bubbles: true,
            detail: {
                requestConfig: transition.requestConfig,
                successful,
                xhr: {
                    status,
                    getResponseHeader: (name) => responseHeaders.get(name.toLowerCase()) || null,
                },
            },
        }),
    );
}

async function flush() {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
}

beforeEach(() => {
    vi.useFakeTimers();
    delete window.htmx;
    window.localStorage.clear();
    window.sessionStorage.clear();
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    renderDom();
});

describe("Zeitstrom live watch", () => {
    it("sends the opaque page scope on watch and follow-ups", async () => {
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(watchResponse(["feed"], { feed: '"feed-v2"' }))
            .mockResolvedValueOnce(
                response({
                    text: "<article>neuer Feed</article>",
                    headers: { "Content-Type": "text/html", ETag: '"feed-v2"' },
                }),
            );
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
        });

        await watcher.checkNow();

        expect(fetchMock.mock.calls[0][1].headers["X-Zeitstrom-Page-Scope"]).toBe("opaque-page-scope");
        expect(fetchMock.mock.calls[1][1].headers["X-Zeitstrom-Page-Scope"]).toBe("opaque-page-scope");
        expect(fetchMock.mock.calls[1][1].headers["If-Match"]).toBe('"feed-v2"');
    });

    it("never swaps a follow-up from a different snapshot and converges A to B to A safely", async () => {
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(watchResponse(["feed"], { feed: '"feed-a"' }))
            .mockResolvedValueOnce(
                response({
                    text: '<article data-sensitivity="elevated">B elevated</article>',
                    headers: { "Content-Type": "text/html", ETag: '"feed-b"' },
                }),
            )
            .mockResolvedValueOnce(watchResponse(["feed"], { feed: '"feed-a"' }))
            .mockResolvedValueOnce(
                response({
                    text: "<article>A safe</article>",
                    headers: { "Content-Type": "text/html", ETag: '"feed-a"' },
                }),
            );
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
        });

        await watcher.checkNow();
        expect(document.body.textContent).not.toContain("B elevated");
        expect(document.getElementById("feed-list").textContent).toContain("alter Feed");
        await watcher.checkNow();

        expect(fetchMock).toHaveBeenCalledTimes(4);
        expect(JSON.parse(fetchMock.mock.calls[2][1].headers["X-Zeitstrom-Validators"])).not.toHaveProperty("feed");
        expect(document.getElementById("feed-list").textContent).toContain("A safe");
        expect(document.body.textContent).not.toContain("B elevated");
    });

    it.each([null, '"wrong"'])("rejects a follow-up with missing or wrong validator %s", async (etag) => {
        const headers = { "Content-Type": "text/html" };
        if (etag) headers.ETag = etag;
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(watchResponse(["feed"], { feed: '"feed-v2"' }))
            .mockResolvedValueOnce(response({ text: "<article>nicht einsetzen</article>", headers }));
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
        });

        await watcher.checkNow();

        expect(document.body.textContent).not.toContain("nicht einsetzen");
        expect(document.getElementById("feed-list").textContent).toContain("alter Feed");
    });

    it("aborts sibling follow-ups and invalidates their generation after one snapshot mismatch", async () => {
        const lateCurrent = deferred();
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(
                watchResponse(
                    ["feed", "current_shift"],
                    { feed: '"feed-a"', current_shift: '"shift-a"' },
                ),
            )
            .mockResolvedValueOnce(
                response({
                    text: "<article>A rejected</article>",
                    headers: { "Content-Type": "text/html", ETag: '"feed-wrong"' },
                }),
            )
            .mockImplementationOnce((_url, options) => {
                lateCurrent.signal = options.signal;
                return lateCurrent.promise;
            })
            .mockResolvedValueOnce(
                watchResponse(["current_shift"], { current_shift: '"shift-b"' }),
            )
            .mockResolvedValueOnce(
                response({
                    text: '<section id="zeitstrom-current-shift-slot">B current</section>',
                    headers: { "Content-Type": "text/html", ETag: '"shift-b"' },
                }),
            )
            .mockResolvedValueOnce(response({ status: 304, headers: { ETag: '"page-b"' } }));
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
        });

        await watcher.checkNow();
        expect(lateCurrent.signal.aborted).toBe(true);
        await watcher.checkNow();
        expect(document.getElementById("zeitstrom-current-shift-slot").textContent).toContain("B current");

        lateCurrent.resolve(
            response({
                text: '<section id="zeitstrom-current-shift-slot">A stale</section>',
                headers: { "Content-Type": "text/html", ETag: '"shift-a"' },
            }),
        );
        await flush();
        await watcher.checkNow();

        expect(document.getElementById("zeitstrom-current-shift-slot").textContent).toContain("B current");
        expect(document.body.textContent).not.toContain("A stale");
    });

    it("aborts late siblings after any follow-up failure while preserving backoff", async () => {
        const lateFeed = deferred();
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(
                watchResponse(
                    ["feed", "current_shift"],
                    { feed: '"feed-a"', current_shift: '"shift-a"' },
                ),
            )
            .mockImplementationOnce((_url, options) => {
                lateFeed.signal = options.signal;
                return lateFeed.promise;
            })
            .mockResolvedValueOnce(
                response({
                    status: 500,
                    text: "<html>temporärer Dienstfehler</html>",
                    headers: { "Content-Type": "text/html" },
                }),
            )
            .mockResolvedValueOnce(watchResponse(["feed"], { feed: '"feed-b"' }))
            .mockResolvedValueOnce(
                response({
                    text: "<article>B current</article>",
                    headers: { "Content-Type": "text/html", ETag: '"feed-b"' },
                }),
            )
            .mockResolvedValueOnce(response({ status: 304, headers: { ETag: '"page-b"' } }));
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
        });

        await watcher.checkNow();
        expect(lateFeed.signal.aborted).toBe(true);
        await watcher.checkNow();
        expect(document.getElementById("feed-list").textContent).toContain("B current");

        lateFeed.resolve(
            response({
                text: "<article>A stale</article>",
                headers: { "Content-Type": "text/html", ETag: '"feed-a"' },
            }),
        );
        await flush();
        await watcher.checkNow();

        expect(document.getElementById("feed-list").textContent).toContain("B current");
        expect(document.body.textContent).not.toContain("A stale");
    });

    it("fails closed on unknown or mismatched component protocol", async () => {
        const navigate = vi.fn();
        const fetchMock = vi.fn().mockResolvedValue(
            watchResponse(["feed_typo"], { feed_typo: '"opaque"' }),
        );
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            navigate,
        });

        await watcher.checkNow();

        expect(fetchMock).toHaveBeenCalledOnce();
        expect(document.getElementById("zeitstrom-live-watch").textContent).toBe("");
        expect(navigate).toHaveBeenCalledOnce();
        expect(watcher.isStopped()).toBe(true);
    });

    it("clears sensitive DOM and reloads when scope or tally topology is stale", async () => {
        const navigate = vi.fn();
        const scopeFetch = vi.fn().mockResolvedValue(
            response({
                status: 409,
                json: { detail: "reload_required" },
                headers: { "Content-Type": "application/json" },
            }),
        );
        const scopeWatcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: scopeFetch,
            navigate,
        });
        await scopeWatcher.checkNow();
        expect(document.getElementById("zeitstrom-live-watch").textContent).toBe("");
        expect(navigate).toHaveBeenCalledOnce();

        renderDom();
        navigate.mockClear();
        const topologyFetch = vi.fn().mockResolvedValue(
            watchResponse(["tally_topology"], { tally_topology: '"topology-v2"' }),
        );
        const topologyWatcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: topologyFetch,
            navigate,
        });
        await topologyWatcher.checkNow();
        expect(document.getElementById("zeitstrom-live-watch").textContent).toBe("");
        expect(navigate).toHaveBeenCalledOnce();
        expect(topologyFetch).toHaveBeenCalledOnce();
    });

    it("commits the normal time-filter HTMX snapshot before checking topology", async () => {
        const navigate = vi.fn();
        const fetchMock = vi.fn().mockImplementation(async (_url, options) => {
            if (fetchMock.mock.calls.length === 1) {
                return response({ status: 304, headers: { ETag: '"page-v1"' } });
            }
            const sent = JSON.parse(options.headers["X-Zeitstrom-Validators"] || "{}");
            if (sent.feed === '"feed-v2"' && sent.tally_topology === '"topology-v2"') {
                return response({ status: 304, headers: { ETag: '"page-v2"' } });
            }
            return watchResponse(["tally_topology"], { tally_topology: '"topology-v2"' });
        });
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            navigate,
        });
        watcher.start();
        await flush();

        const transition = configureTimeFilter("shift-v2");
        await flush();

        expect(transition.requestConfig.headers["X-Zeitstrom-Filter-Transition"]).toBe("time_filter");
        expect(transition.requestConfig.headers["X-Zeitstrom-Page-Scope"]).toBe("opaque-page-scope");
        expect(JSON.parse(transition.requestConfig.headers["X-Zeitstrom-Previous-State"])).toEqual({
            date: "2026-08-16",
            time_filter: "all",
            type: "",
            doc_type: "",
            view: "",
        });
        expect(JSON.parse(transition.requestConfig.headers["X-Zeitstrom-Validators"])).toEqual({
            tally_topology: '"topology-v1"',
        });
        expect(fetchMock).toHaveBeenCalledOnce();

        finishTimeFilterRequest(transition, {
            etag: '"feed-v2"',
            validators: { feed: '"feed-v2"', tally_topology: '"topology-v2"' },
        });
        await flush();

        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(JSON.parse(fetchMock.mock.calls[1][1].headers["X-Zeitstrom-Validators"])).toMatchObject({
            feed: '"feed-v2"',
            tally_topology: '"topology-v2"',
        });
        expect(navigate).not.toHaveBeenCalled();
        expect(document.getElementById("tally-panel-slot")).not.toBeNull();
        watcher.stop();
    });

    it("defers public and focus checks until a pending time-filter transition commits", async () => {
        const navigate = vi.fn();
        const fetchMock = vi.fn().mockImplementation(async (_url, options) => {
            if (fetchMock.mock.calls.length <= 2) {
                return response({ status: 304, headers: { ETag: '"page-v1"' } });
            }
            const sent = JSON.parse(options.headers["X-Zeitstrom-Validators"] || "{}");
            if (sent.feed === '"feed-v2"' && sent.tally_topology === '"topology-v2"') {
                return response({ status: 304, headers: { ETag: '"page-v2"' } });
            }
            return watchResponse(["tally_topology"], { tally_topology: '"topology-v2"' });
        });
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            navigate,
        });
        watcher.start();
        await flush();

        const docType = document.createElement("select");
        docType.id = "filter-doc-type";
        docType.innerHTML = '<option value="contact" selected>Kontakt</option>';
        document.getElementById("zeitstrom-live-watch").append(docType);
        const docTypeRequest = {
            elt: docType,
            headers: {},
            parameters: {
                date: "2026-08-16",
                time_filter: "all",
                type: "",
                doc_type: "",
                view: "",
            },
        };
        docType.dispatchEvent(
            new CustomEvent("htmx:configRequest", { bubbles: true, detail: docTypeRequest }),
        );
        await flush();
        docType.dispatchEvent(
            new CustomEvent("htmx:afterRequest", {
                bubbles: true,
                detail: { requestConfig: docTypeRequest, successful: true, xhr: { status: 200 } },
            }),
        );
        expect(fetchMock).toHaveBeenCalledTimes(2);

        const transition = configureTimeFilter("shift-v2");
        await watcher.checkNow();
        window.dispatchEvent(new Event("focus"));
        await flush();

        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(navigate).not.toHaveBeenCalled();
        expect(document.getElementById("zeitstrom-live-watch").textContent).not.toBe("");

        finishTimeFilterRequest(transition, {
            etag: '"feed-v2"',
            validators: { feed: '"feed-v2"', tally_topology: '"topology-v2"' },
        });
        await flush();

        expect(fetchMock).toHaveBeenCalledTimes(3);
        expect(JSON.parse(fetchMock.mock.calls[2][1].headers["X-Zeitstrom-Validators"])).toMatchObject({
            feed: '"feed-v2"',
            tally_topology: '"topology-v2"',
        });
        expect(navigate).not.toHaveBeenCalled();
        watcher.stop();
    });

    it.each(["hidden", "offline", "pagehide"])(
        "discards a pending transition after %s changes the generation and resumes one poll",
        async (lifecycle) => {
            const navigate = vi.fn();
            const fetchMock = vi
                .fn()
                .mockResolvedValueOnce(response({ status: 304, headers: { ETag: '"page-v1"' } }))
                .mockResolvedValueOnce(
                    watchResponse(["tally_topology"], { tally_topology: '"topology-current"' }),
                );
            const watcher = window.createZeitstromLiveWatch(
                document.getElementById("zeitstrom-live-watch"),
                { fetch: fetchMock, navigate },
            );
            watcher.start();
            await flush();

            const transition = configureTimeFilter("shift-v2");
            if (lifecycle === "hidden") {
                Object.defineProperty(document, "visibilityState", {
                    configurable: true,
                    value: "hidden",
                });
                document.dispatchEvent(new Event("visibilitychange"));
            } else if (lifecycle === "offline") {
                Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
                window.dispatchEvent(new Event("offline"));
            } else {
                window.dispatchEvent(new Event("pagehide"));
            }

            finishTimeFilterRequest(transition, {
                etag: '"feed-stale"',
                validators: { feed: '"feed-stale"', tally_topology: '"topology-stale"' },
            });

            if (lifecycle === "hidden") {
                Object.defineProperty(document, "visibilityState", {
                    configurable: true,
                    value: "visible",
                });
                document.dispatchEvent(new Event("visibilitychange"));
            } else if (lifecycle === "offline") {
                Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
                window.dispatchEvent(new Event("online"));
            } else {
                window.dispatchEvent(new Event("pageshow"));
            }
            await flush();
            await watcher.checkNow();

            expect(fetchMock).toHaveBeenCalledTimes(2);
            expect(fetchMock.mock.calls[1][1].headers).not.toHaveProperty(
                "X-Zeitstrom-Validators",
            );
            expect(navigate).toHaveBeenCalledOnce();
            expect(document.getElementById("zeitstrom-live-watch").textContent).toBe("");
        },
    );

    it("correlates a same-value replacement after hx-sync aborts the previous request", async () => {
        let replacementSignal;
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(response({ status: 304, headers: { ETag: '"page-v1"' } }))
            .mockImplementationOnce((_url, options) => {
                replacementSignal = options.signal;
                return new Promise((_resolve, reject) => {
                    options.signal.addEventListener("abort", () => {
                        reject(new DOMException("aborted", "AbortError"));
                    });
                });
            })
            .mockResolvedValueOnce(response({ status: 304, headers: { ETag: '"page-v2"' } }));
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
        });
        watcher.start();
        await flush();

        const aborted = configureTimeFilter("shift-v2");
        finishTimeFilterRequest(aborted, { status: 0, successful: false });
        await flush();
        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(replacementSignal.aborted).toBe(false);

        const replacement = configureTimeFilter("shift-v2", aborted.tab);
        expect(replacementSignal.aborted).toBe(true);
        expect(JSON.parse(replacement.requestConfig.headers["X-Zeitstrom-Validators"])).toEqual({
            tally_topology: '"topology-v1"',
        });
        expect(JSON.parse(replacement.requestConfig.headers["X-Zeitstrom-Previous-State"])).toEqual({
            date: "2026-08-16",
            time_filter: "all",
            type: "",
            doc_type: "",
            view: "",
        });
        finishTimeFilterRequest(replacement, {
            etag: '"feed-v2"',
            validators: { feed: '"feed-v2"', tally_topology: '"topology-v2"' },
        });
        await flush();

        expect(fetchMock).toHaveBeenCalledTimes(3);
        expect(JSON.parse(fetchMock.mock.calls[2][1].headers["X-Zeitstrom-Validators"])).toMatchObject({
            feed: '"feed-v2"',
            tally_topology: '"topology-v2"',
        });
        watcher.stop();
    });

    it.each([
        ["missing", null, null],
        [
            "mismatched",
            '"feed-v2"',
            JSON.stringify({ feed: '"other-feed"', tally_topology: '"topology-v2"' }),
        ],
    ])("fails closed when the time-filter snapshot validators are %s", async (_case, etag, rawValidators) => {
        const navigate = vi.fn();
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(response({ status: 304, headers: { ETag: '"page-v1"' } }))
            .mockResolvedValueOnce(
                watchResponse(["tally_topology"], { tally_topology: '"topology-v2"' }),
            );
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            navigate,
        });
        watcher.start();
        await flush();

        const transition = configureTimeFilter("shift-v2");
        finishTimeFilterRequest(transition, {
            etag,
            validators: rawValidators ? JSON.parse(rawValidators) : null,
        });
        await flush();

        expect(fetchMock).toHaveBeenCalledTimes(2);
        const sent = JSON.parse(fetchMock.mock.calls[1][1].headers["X-Zeitstrom-Validators"] || "{}");
        expect(sent).not.toHaveProperty("feed");
        expect(sent).not.toHaveProperty("tally_topology");
        expect(navigate).toHaveBeenCalledOnce();
        expect(document.getElementById("zeitstrom-live-watch").textContent).toBe("");
    });

    it("ignores a stale time-filter response even when the selected value returns to A", async () => {
        const navigate = vi.fn();
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(response({ status: 304, headers: { ETag: '"page-v1"' } }))
            .mockResolvedValueOnce(response({ status: 304, headers: { ETag: '"page-a2"' } }));
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            navigate,
        });
        watcher.start();
        await flush();

        const firstA = configureTimeFilter("shift-a");
        configureTimeFilter("shift-b", firstA.tab);
        const currentA = configureTimeFilter("shift-a", firstA.tab);

        finishTimeFilterRequest(firstA, {
            etag: '"feed-stale"',
            validators: { feed: '"feed-stale"', tally_topology: '"topology-stale"' },
        });
        await flush();
        expect(fetchMock).toHaveBeenCalledOnce();

        finishTimeFilterRequest(currentA, {
            etag: '"feed-a2"',
            validators: { feed: '"feed-a2"', tally_topology: '"topology-a2"' },
        });
        await flush();

        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(JSON.parse(fetchMock.mock.calls[1][1].headers["X-Zeitstrom-Validators"])).toMatchObject({
            feed: '"feed-a2"',
            tally_topology: '"topology-a2"',
        });
        expect(navigate).not.toHaveBeenCalled();
        watcher.stop();
    });

    it("keeps an open NUMBER form while applying authoritative count spans", async () => {
        const form = document.querySelector("[data-offline-tally-number-form]");
        const hook = vi.fn();
        window.addEventListener("zeitstrom:tally-counts-updated", hook, { once: true });
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(watchResponse(["tally_counts"], { tally_counts: '"count-v2"' }))
            .mockResolvedValueOnce(
                response({
                    text: '<span id="tally-count-type-1" data-confirmed-count="5" aria-label="5 Kontakte für Kontakt">5</span>',
                    headers: { "Content-Type": "text/html", ETag: '"count-v2"' },
                }),
            );
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            random: () => 0.5,
        });

        await watcher.pollNow();

        const count = document.getElementById("tally-count-type-1");
        expect(count.textContent).toBe("5");
        expect(count.dataset.confirmedCount).toBe("5");
        expect(count.getAttribute("aria-label")).toBe("5 Kontakte für Kontakt");
        expect(form.isConnected).toBe(true);
        expect(form.querySelector("input").value).toBe("7");
        expect(hook).toHaveBeenCalledOnce();
        expect(fetchMock.mock.calls[1][1].headers["X-Zeitstrom-Watch"]).toBe("1");
    });

    it("refreshes only the feed and never destroys an open tally form", async () => {
        const form = document.querySelector("[data-offline-tally-number-form]");
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(watchResponse(["feed"], { feed: '"feed-v2"' }))
            .mockResolvedValueOnce(
                response({
                    text: "<article>neuer Feed</article>",
                    headers: { "Content-Type": "text/html", ETag: '"feed-v2"' },
                }),
            );
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            random: () => 0.5,
        });

        await watcher.pollNow();

        expect(document.getElementById("feed-list").textContent).toContain("neuer Feed");
        expect(form.isConnected).toBe(true);
        const followupUrl = new URL(fetchMock.mock.calls[1][0], "https://example.test");
        expect(followupUrl.searchParams.get("feed_only")).toBe("1");
        expect(followupUrl.searchParams.get("date")).toBe("2026-08-16");
        expect(followupUrl.searchParams.get("time_filter")).toBe("all");
        expect(followupUrl.searchParams.has("type")).toBe(true);
        expect(followupUrl.searchParams.has("doc_type")).toBe(true);
        expect(followupUrl.searchParams.has("view")).toBe(true);
        expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    it("uses the HTMX swap pipeline and preserves viewport, filter, and collapse state", async () => {
        let scrollY = 640;
        Object.defineProperty(window, "scrollX", { configurable: true, get: () => 0 });
        Object.defineProperty(window, "scrollY", { configurable: true, get: () => scrollY });
        window.scrollTo = vi.fn((_x, y) => {
            scrollY = y;
        });
        window.localStorage.setItem("zeitstrom-collapse-state", "expanded");
        document.querySelector('[name="type"]').value = "events";
        const swap = vi.fn((target, html) => {
            // Simulate a swap implementation that moves the viewport. The
            // watcher must restore it after inserting the new feed cards.
            scrollY = 0;
            target.innerHTML = html;
        });
        window.htmx = { swap };
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(watchResponse(["feed"], { feed: '"feed-v2"' }))
            .mockResolvedValueOnce(
                response({
                    text: '<article x-data="expandableCard" hx-get="/detail/">neuer Feed</article>',
                    headers: { "Content-Type": "text/html", ETag: '"feed-v2"' },
                }),
            );
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
        });

        await watcher.pollNow();

        const target = document.getElementById("feed-list");
        expect(swap).toHaveBeenCalledOnce();
        expect(swap).toHaveBeenCalledWith(
            target,
            expect.stringContaining('x-data="expandableCard"'),
            expect.objectContaining({ swapStyle: "innerHTML" }),
        );
        expect(target.querySelector("[x-data]").getAttribute("hx-get")).toBe("/detail/");
        expect(window.scrollY).toBe(640);
        expect(window.localStorage.getItem("zeitstrom-collapse-state")).toBe("expanded");
        expect(document.querySelector('[name="type"]').value).toBe("events");
    });

    it("preserves individual feed-card expansion state across a live swap", async () => {
        document.getElementById("feed-list").innerHTML = `
            <div x-data="expandableCard">
                <button aria-controls="event-detail-1" aria-expanded="true">offen</button>
            </div>
            <div x-data="expandableActivityCard">
                <button aria-controls="activity-detail-2" aria-expanded="false">zu</button>
            </div>`;
        window.htmx = {
            swap: vi.fn((target, html) => {
                target.innerHTML = html;
            }),
        };
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(watchResponse(["feed"], { feed: '"feed-v2"' }))
            .mockResolvedValueOnce(
                response({
                    text: `
                        <div x-data="expandableCard">
                            <button aria-controls="event-detail-1" aria-expanded="false">neu offen</button>
                        </div>
                        <div x-data="expandableActivityCard">
                            <button aria-controls="activity-detail-2" aria-expanded="false">neu zu</button>
                        </div>`,
                    headers: { "Content-Type": "text/html", ETag: '"feed-v2"' },
                }),
            );
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
        });

        await watcher.pollNow();

        const freshEvent = document.querySelector('[x-data="expandableCard"]');
        const freshActivity = document.querySelector('[x-data="expandableActivityCard"]');
        expect(freshEvent.dataset.zeitstromExpanded).toBe("true");
        expect(freshActivity.dataset.zeitstromExpanded).toBe("false");
    });

    it("invalidates a local zeitstrom change without starting a competing immediate refresh cascade", async () => {
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(
                watchResponse([], {}),
            )
            .mockResolvedValueOnce(
                watchResponse(
                    ["feed", "tally_counts", "current_shift"],
                    {
                        feed: '"feed-v2"',
                        tally_counts: '"count-v2"',
                        current_shift: '"shift-v2"',
                    },
                ),
            )
            .mockResolvedValueOnce(
                response({
                    text: '<div class="space-y-2">neuer Feed</div>',
                    headers: { "Content-Type": "text/html", ETag: '"feed-v2"' },
                }),
            )
            .mockResolvedValueOnce(
                response({
                    text: '<span id="tally-count-type-1" data-confirmed-count="3" aria-label="3 Kontakte">3</span>',
                    headers: { "Content-Type": "text/html", ETag: '"count-v2"' },
                }),
            )
            .mockResolvedValueOnce(
                response({
                    text: '<section id="zeitstrom-current-shift-slot">neuer Dienst</section>',
                    headers: { "Content-Type": "text/html", ETag: '"shift-v2"' },
                }),
            );
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
        });
        watcher.start();
        await flush();

        document.body.dispatchEvent(new CustomEvent("zeitstrom-changed", { bubbles: true }));
        document.body.dispatchEvent(new CustomEvent("zeitstrom-changed", { bubbles: true }));
        await flush();

        // #1911 owns the immediate feed refresh. Even duplicate local
        // signals merely reset one 12-second convergence timer.
        expect(fetchMock).toHaveBeenCalledTimes(1);

        await vi.advanceTimersByTimeAsync(12_000);
        await flush();

        expect(fetchMock).toHaveBeenCalledTimes(5);
        const urls = fetchMock.mock.calls.map(([url]) => new URL(url, "https://example.test").pathname);
        expect(urls).toEqual([
            "/api/v1/zeitstrom/watch/",
            "/api/v1/zeitstrom/watch/",
            "/partials/zeitstrom/feed/",
            "/partials/zeitstrom/tally-counts/",
            "/partials/zeitstrom/current-shift/",
        ]);
        expect(document.querySelector("[data-offline-tally-number-form]").isConnected).toBe(true);
        expect(document.getElementById("zeitstrom-current-shift-slot").textContent).toContain("neuer Dienst");
        watcher.stop();
    });

    it("exposes a deterministic check hook without exposing validators or state", async () => {
        const fetchMock = vi.fn().mockResolvedValue(response({ status: 304 }));
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
        });

        expect(watcher.checkNow).toBeTypeOf("function");
        expect(watcher.validators).toBeUndefined();
        expect(watcher.state).toBeUndefined();
        await watcher.checkNow();

        expect(fetchMock).toHaveBeenCalledOnce();
    });

    it("keeps component validators in a compact header, never in URL state or storage", async () => {
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(watchResponse(["feed"], { feed: '"feed-v1"' }))
            .mockResolvedValueOnce(
                response({
                    text: "<article>initialer Feed</article>",
                    headers: { "Content-Type": "text/html", ETag: '"feed-v1"' },
                }),
            )
            .mockResolvedValueOnce(response({ status: 304, headers: { ETag: '"page-v1"' } }));
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
        });

        await watcher.pollNow();
        await watcher.pollNow();

        const secondUrl = new URL(fetchMock.mock.calls[2][0]);
        expect([...secondUrl.searchParams.keys()]).toEqual([
            "date",
            "time_filter",
            "type",
            "doc_type",
            "view",
        ]);
        expect(JSON.parse(fetchMock.mock.calls[2][1].headers["X-Zeitstrom-Validators"])).toEqual({
            feed: '"feed-v1"',
            tally_topology: '"topology-v1"',
        });
        expect(window.localStorage.length).toBe(0);
        expect(window.sessionStorage.length).toBe(0);
    });

    it("aborts and discards an old generation after a filter change", async () => {
        const stale = deferred();
        const fetchMock = vi
            .fn()
            .mockImplementationOnce((_url, options) => {
                expect(options.signal.aborted).toBe(false);
                return stale.promise;
            })
            .mockResolvedValueOnce(watchResponse());
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            random: () => 0.5,
        });
        watcher.start();
        await flush();
        const firstSignal = fetchMock.mock.calls[0][1].signal;

        document.querySelector('[name="type"]').value = "events";
        window.dispatchEvent(new CustomEvent("zeitstrom:filter-changed"));
        await flush();

        expect(firstSignal.aborted).toBe(true);
        expect(fetchMock).toHaveBeenCalledTimes(2);
        stale.resolve(watchResponse(["feed"], { feed: '"stale"' }));
        await flush();

        expect(document.getElementById("feed-list").textContent).toContain("alter Feed");
        expect(fetchMock).toHaveBeenCalledTimes(2);
        watcher.stop();
    });

    it("keeps filter-independent validators and refreshes only feed after a type change", async () => {
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(
                watchResponse(["tally_counts", "current_shift", "focus_box"], {
                    tally_counts: '"count-v1"',
                    current_shift: '"shift-v1"',
                    focus_box: '"focus-v1"',
                }),
            )
            .mockResolvedValueOnce(
                response({
                    text: '<span id="tally-count-type-1" data-confirmed-count="2" aria-label="2 Kontakte">2</span>',
                    headers: { "Content-Type": "text/html", ETag: '"count-v1"' },
                }),
            )
            .mockResolvedValueOnce(
                response({
                    text: '<section id="zeitstrom-current-shift-slot">Dienst</section>',
                    headers: { "Content-Type": "text/html", ETag: '"shift-v1"' },
                }),
            )
            .mockResolvedValueOnce(
                response({
                    text: '<aside id="zeitstrom-focus-box-slot">Fokus</aside>',
                    headers: { "Content-Type": "text/html", ETag: '"focus-v1"' },
                }),
            )
            .mockResolvedValueOnce(watchResponse(["feed"], { feed: '"feed-v2"' }))
            .mockResolvedValueOnce(
                response({
                    text: "<article>gefilterter Feed</article>",
                    headers: { "Content-Type": "text/html", ETag: '"feed-v2"' },
                }),
            );
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
        });
        watcher.start();
        await watcher.checkNow();

        document.querySelector('[name="type"]').value = "events";
        window.dispatchEvent(
            new CustomEvent("zeitstrom:filter-changed", { detail: { name: "type", value: "events" } }),
        );
        await flush();
        await flush();

        const paths = fetchMock.mock.calls.map(([url]) => new URL(url, "https://example.test").pathname);
        expect(paths).toEqual([
            "/api/v1/zeitstrom/watch/",
            "/partials/zeitstrom/tally-counts/",
            "/partials/zeitstrom/current-shift/",
            "/partials/zeitstrom/focus-box/",
            "/api/v1/zeitstrom/watch/",
            "/partials/zeitstrom/feed/",
        ]);
        expect(JSON.parse(fetchMock.mock.calls[4][1].headers["X-Zeitstrom-Validators"])).toEqual({
            tally_counts: '"count-v1"',
            current_shift: '"shift-v1"',
            focus_box: '"focus-v1"',
            tally_topology: '"topology-v1"',
        });
        watcher.stop();
    });

    it("uses exponential backoff and resets it after a 304", async () => {
        const fetchMock = vi
            .fn()
            .mockRejectedValueOnce(new TypeError("offline"))
            .mockResolvedValueOnce(response({ status: 304, headers: { ETag: '"same"' } }))
            .mockResolvedValueOnce(response({ status: 304, headers: { ETag: '"same"' } }));
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            random: () => 0.5,
        });
        watcher.start();
        await flush();

        await vi.advanceTimersByTimeAsync(23_999);
        expect(fetchMock).toHaveBeenCalledTimes(1);
        await vi.advanceTimersByTimeAsync(1);
        expect(fetchMock).toHaveBeenCalledTimes(2);
        await flush();

        await vi.advanceTimersByTimeAsync(11_999);
        expect(fetchMock).toHaveBeenCalledTimes(2);
        await vi.advanceTimersByTimeAsync(1);
        expect(fetchMock).toHaveBeenCalledTimes(3);
        watcher.stop();
    });

    it.each([429, 500])("backs off on a %s HTML watch error instead of treating it as login", async (status) => {
        const navigate = vi.fn();
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(
                response({
                    status,
                    text: "<html><body>temporärer Fehler</body></html>",
                    headers: { "Content-Type": "text/html" },
                }),
            )
            .mockResolvedValueOnce(response({ status: 304 }));
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            navigate,
            random: () => 0.5,
        });
        watcher.start();
        await flush();

        expect(watcher.isStopped()).toBe(false);
        expect(navigate).not.toHaveBeenCalled();
        await vi.advanceTimersByTimeAsync(24_000);

        expect(fetchMock).toHaveBeenCalledTimes(2);
        watcher.stop();
    });

    it("backs off on an HTML follow-up 500 without navigating or inserting the body", async () => {
        const navigate = vi.fn();
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(watchResponse(["feed"], { feed: '"feed-v2"' }))
            .mockResolvedValueOnce(
                response({
                    status: 500,
                    text: "<html><body>temporärer Feed-Fehler</body></html>",
                    headers: { "Content-Type": "text/html" },
                }),
            )
            .mockResolvedValueOnce(response({ status: 304 }));
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            navigate,
            random: () => 0.5,
        });
        watcher.start();
        await flush();

        expect(watcher.isStopped()).toBe(false);
        expect(navigate).not.toHaveBeenCalled();
        expect(document.getElementById("feed-list").textContent).toContain("alter Feed");
        await vi.advanceTimersByTimeAsync(24_000);

        expect(fetchMock).toHaveBeenCalledTimes(3);
        watcher.stop();
    });

    it("removes timers and aborts in-flight work while hidden, offline, or page-hidden", async () => {
        const pending = deferred();
        const fetchMock = vi.fn().mockReturnValue(pending.promise);
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
        });
        watcher.start();
        await flush();
        const signal = fetchMock.mock.calls[0][1].signal;

        Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
        document.dispatchEvent(new Event("visibilitychange"));

        expect(signal.aborted).toBe(true);
        expect(vi.getTimerCount()).toBe(0);
        window.dispatchEvent(new Event("pagehide"));
        window.dispatchEvent(new Event("offline"));
        expect(fetchMock).toHaveBeenCalledTimes(1);
        watcher.stop();
    });

    it("stops on a JSON 401 and navigates to the supplied login URL without inserting its body", async () => {
        const navigate = vi.fn();
        const fetchMock = vi.fn().mockResolvedValue(
            response({
                status: 401,
                json: { detail: "authentication_required", login_url: "/login/?next=/" },
                headers: { "Content-Type": "application/json" },
            }),
        );
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            navigate,
        });

        await watcher.pollNow();

        expect(navigate).toHaveBeenCalledWith("/login/?next=/");
        expect(document.body.textContent).not.toContain("authentication_required");
        expect(watcher.isStopped()).toBe(true);
    });

    it("treats a successful unexpected HTML watch response as login, not as a retryable server error", async () => {
        const navigate = vi.fn();
        const fetchMock = vi.fn().mockResolvedValue(
            response({
                text: "<html><body><form>Login</form></body></html>",
                headers: { "Content-Type": "text/html" },
                url: "/api/v1/zeitstrom/watch/",
            }),
        );
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            navigate,
        });

        await watcher.checkNow();

        expect(navigate).toHaveBeenCalledWith("/login/");
        expect(document.getElementById("zeitstrom-live-watch").textContent).toBe("");
        expect(watcher.isStopped()).toBe(true);
    });

    it("treats a successful login document from a follow-up as auth loss", async () => {
        const navigate = vi.fn();
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(watchResponse(["feed"], { feed: '"feed-v2"' }))
            .mockResolvedValueOnce(
                response({
                    text: '<html><body><form action="/login/">Anmelden</form></body></html>',
                    headers: { "Content-Type": "text/html" },
                }),
            );
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            navigate,
        });

        await watcher.checkNow();

        expect(navigate).toHaveBeenCalledWith("/login/");
        expect(document.getElementById("zeitstrom-live-watch").textContent).toBe("");
        expect(watcher.isStopped()).toBe(true);
    });

    it("stops and fully navigates away from stale DOM on a watch 403", async () => {
        const navigate = vi.fn();
        const fetchMock = vi.fn().mockResolvedValue(
            response({
                status: 403,
                json: { detail: "forbidden" },
                headers: { "Content-Type": "application/json" },
            }),
        );
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            navigate,
        });

        await watcher.checkNow();

        expect(navigate).toHaveBeenCalledWith(window.location.href);
        expect(document.body.textContent).not.toContain("forbidden");
        expect(watcher.isStopped()).toBe(true);
    });

    it("stops and navigates without inserting a forbidden follow-up body", async () => {
        const navigate = vi.fn();
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(watchResponse(["feed"], { feed: '"feed-v2"' }))
            .mockResolvedValueOnce(
                response({
                    status: 403,
                    text: "<html><body>Verbotener Inhalt</body></html>",
                    headers: { "Content-Type": "text/html" },
                }),
            );
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            navigate,
        });

        await watcher.checkNow();

        expect(navigate).toHaveBeenCalledWith(window.location.href);
        expect(document.getElementById("feed-list")).toBeNull();
        expect(document.body.textContent).not.toContain("Verbotener Inhalt");
        expect(watcher.isStopped()).toBe(true);
    });

    // Refs #1951: Der Watch-Poll ist der einzige Teil der App, der aus sich
    // heraus navigiert. Faellt die Session weg, verlaesst er die sensible Seite
    // — genau richtig bei einem Session-Ablauf, aber fatal WAEHREND eines
    // Logouts: dort ist die Navigation nach ``/login/`` schon unterwegs, und ein
    // zweites ``location.assign`` bricht sie ab (``net::ERR_ABORTED``). Der
    // Nutzer landete dann auf ``/login/?next=<Partial-URL>``.
    it("verlaesst die sensible Seite, wenn der Poll auf die Login-Seite umgeleitet wird", async () => {
        const navigate = vi.fn();
        const fetchMock = vi.fn().mockResolvedValue(
            response({ redirected: true, url: "http://localhost/login/" }),
        );
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            navigate,
        });

        await watcher.checkNow();

        expect(navigate).toHaveBeenCalledWith("http://localhost/login/");
        expect(watcher.isStopped()).toBe(true);
    });

    it("ueberholt eine laufende Logout-Navigation nicht (Refs #1951)", async () => {
        const navigate = vi.fn();
        const fetchMock = vi.fn().mockResolvedValue(
            response({
                redirected: true,
                url: "http://localhost/login/?next=/partials/zeitstrom/current-shift/",
            }),
        );
        window.__logoutInFlight = Date.now();
        try {
            const watcher = window.createZeitstromLiveWatch(
                document.getElementById("zeitstrom-live-watch"),
                { fetch: fetchMock, navigate },
            );

            await watcher.checkNow();

            // Der schutzrelevante Teil passiert weiter: Polling aus, sensible
            // Inhalte raus.
            expect(watcher.isStopped()).toBe(true);
            expect(document.getElementById("zeitstrom-live-watch").textContent).toBe("");
            // Aber KEINE konkurrierende Navigation — der Logout bringt die
            // Seite selbst nach /login/.
            expect(navigate).not.toHaveBeenCalled();
        } finally {
            delete window.__logoutInFlight;
        }
    });

    it("navigiert wieder, wenn die Logout-Marke abgelaufen ist (Refs #1951)", async () => {
        const navigate = vi.fn();
        const fetchMock = vi.fn().mockResolvedValue(
            response({ redirected: true, url: "http://localhost/login/" }),
        );
        // Logout, der nie navigiert hat: aelter als das 10-s-Fenster. Der Poll
        // muss die sensible Seite bei spaeterem Session-Ablauf wieder
        // verlassen duerfen.
        window.__logoutInFlight = Date.now() - 11_000;
        try {
            const watcher = window.createZeitstromLiveWatch(
                document.getElementById("zeitstrom-live-watch"),
                { fetch: fetchMock, navigate },
            );

            await watcher.checkNow();

            expect(navigate).toHaveBeenCalledWith("http://localhost/login/");
        } finally {
            delete window.__logoutInFlight;
        }
    });
});
