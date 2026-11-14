// Hiding the ban details of the Zeitstrom: the figure in the KPI row and the close
// button of the detail block, the state kept across slot swaps, the focus contract.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

beforeAll(async () => {
    window.__ZEITSTROM_WATCH_TEST__ = true;
    await import("../../src/static/js/focus-management.js");
    await import("../../src/static/js/zeitstrom-live-watch.js");
    await import("../../src/static/js/ban-details-toggle.js");
});

function kpiSlot({ hidden = false, fp = "fp1", count = 2 } = {}) {
    return `<div id="zeitstrom-kpi-slot" data-ban-fingerprint="${fp}" data-ban-details-hidden="${hidden}">
        <ul><li><button type="submit" form="zeitstrom-ban-details-form" id="zeitstrom-ban-details-toggle"
            data-testid="zeitstrom-kpi-bans" name="ban_details" value="${hidden ? "show" : "hide:" + fp}"
            aria-expanded="${hidden ? "false" : "true"}" aria-controls="zeitstrom-ban-details">${count} Hausverbote aktiv</button></li></ul>
    </div>`;
}

function bansSlot({ hidden = false, fp = "fp1", handover = false } = {}) {
    const close = handover
        ? ""
        : `<button type="submit" form="zeitstrom-ban-details-form" id="zeitstrom-ban-details-close"
            name="ban_details" value="hide:${fp}" aria-controls="zeitstrom-ban-details"
            aria-label="Hausverbot-Details ausblenden">×</button>`;
    return `<div id="zeitstrom-bans-slot" data-fingerprint="${fp}" data-ban-details-hidden="${hidden}">
        <div id="zeitstrom-ban-details"${hidden ? " hidden" : ""}><p>Hausverbot: Person</p>${close}</div>
    </div>`;
}

function emptyKpiSlot() {
    return '<div id="zeitstrom-kpi-slot" data-ban-fingerprint="" data-ban-details-hidden="false"><ul></ul></div>';
}

function emptyBansSlot() {
    return '<div id="zeitstrom-bans-slot" data-fingerprint="" data-ban-details-hidden="false"></div>';
}

// Records every request of the toggle module to the live watch.
function staleRequests() {
    const seen = [];
    const listener = () => seen.push(Date.now());
    window.addEventListener("zeitstrom:ban-slots-stale", listener);
    seen.stop = () => window.removeEventListener("zeitstrom:ban-slots-stale", listener);
    return seen;
}

function renderPage(options = {}) {
    document.body.innerHTML = `<main id="zeitstrom-live-watch"
            data-page-scope="scope" data-watch-url="/w/" data-feed-url="/f/" data-tally-counts-url="/t/"
            data-kpi-url="/partials/zeitstrom/kpi/" data-focus-box-url="/fb/" data-bans-url="/partials/zeitstrom/bans/">
        <form id="zeitstrom-filter-state"><input name="date" value="2026-08-16"><input name="time_filter" value="all">
            <input name="type" value=""><input name="doc_type" value=""><input name="view" value=""></form>
        <form id="zeitstrom-ban-details-form" method="post" action="/zeitstrom/ban-details/">
            <input type="hidden" name="csrfmiddlewaretoken" value="token-1"><input type="hidden" name="next" value="/zeitstrom/">
        </form>
        ${kpiSlot(options)}${bansSlot(options)}<div id="feed-list"></div></main>`;
}

function swap(id, html) {
    document.getElementById(id).outerHTML = html;
}

async function settle() {
    for (let i = 0; i < 8; i++) await Promise.resolve();
}

function answer(body, ok = true) {
    return { ok, json: async () => body };
}

const details = () => document.getElementById("zeitstrom-ban-details");
const toggle = () => document.getElementById("zeitstrom-ban-details-toggle");
const close = () => document.getElementById("zeitstrom-ban-details-close");

beforeEach(() => {
    globalThis.fetch = vi.fn();
    renderPage();
});

afterEach(() => {
    vi.restoreAllMocks();
});

describe("close button and figure", () => {
    it("hides the block, moves the focus to the figure and posts the fingerprint", async () => {
        fetch.mockResolvedValue(answer({ hidden: true, fingerprint: "fp1" }));
        close().focus();

        close().click();
        await settle();

        expect(details().hidden).toBe(true);
        expect(toggle().getAttribute("aria-expanded")).toBe("false");
        expect(toggle().value).toBe("show");
        expect(document.activeElement).toBe(toggle());
        const [url, init] = fetch.mock.calls[0];
        expect(url).toContain("/zeitstrom/ban-details/");
        expect(init.method).toBe("POST");
        expect(init.body.get("ban_details")).toBe("hide:fp1");
        expect(init.body.get("csrfmiddlewaretoken")).toBe("token-1");
        expect(init.headers.Accept).toBe("application/json");
        expect(document.getElementById("zeitstrom-bans-slot").dataset.banDetailsHidden).toBe("true");
        expect(document.getElementById("zeitstrom-kpi-slot").dataset.banDetailsHidden).toBe("true");
    });

    it("shows the block again through the figure and tells the server", async () => {
        renderPage({ hidden: true });
        fetch.mockResolvedValue(answer({ hidden: false, fingerprint: "fp1" }));

        toggle().click();
        await settle();

        expect(details().hidden).toBe(false);
        expect(toggle().getAttribute("aria-expanded")).toBe("true");
        expect(toggle().value).toBe("hide:fp1");
        expect(fetch.mock.calls[0][1].body.get("ban_details")).toBe("show");
    });

    it("hides with the fingerprint of the list on screen, not a stale button value", async () => {
        fetch.mockResolvedValue(answer({ hidden: true, fingerprint: "fp1" }));
        toggle().value = "hide:old";

        toggle().click();
        await settle();

        expect(fetch.mock.calls[0][1].body.get("ban_details")).toBe("hide:fp1");
    });

    it("takes the server's word when the list changed meanwhile", async () => {
        fetch.mockResolvedValue(answer({ hidden: false, fingerprint: "fp2" }));

        close().click();
        await settle();

        expect(details().hidden).toBe(false);
        expect(toggle().getAttribute("aria-expanded")).toBe("true");
    });

    it("takes the block back when the request fails", async () => {
        fetch.mockRejectedValue(new Error("offline"));

        close().click();
        await settle();

        expect(details().hidden).toBe(false);
        expect(toggle().getAttribute("aria-expanded")).toBe("true");
    });

    it("takes the block back when the server refuses", async () => {
        fetch.mockResolvedValue(answer({}, false));

        close().click();
        await settle();

        expect(details().hidden).toBe(false);
    });

    it("sends one request while another is in flight", async () => {
        let release;
        fetch.mockReturnValue(new Promise((resolve) => (release = resolve)));

        close().click();
        toggle().click();
        release(answer({ hidden: true, fingerprint: "fp1" }));
        await settle();

        expect(fetch).toHaveBeenCalledTimes(1);
    });

    it("leaves the native form post alone for other buttons", async () => {
        const other = document.createElement("button");
        other.type = "submit";
        document.body.append(other);

        other.click();
        await settle();

        expect(fetch).not.toHaveBeenCalled();
    });
});

describe("state across slot swaps", () => {
    it("keeps the hidden state when both slots are swapped with the stored choice", async () => {
        renderPage({ hidden: true });

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true }));
        swap("zeitstrom-bans-slot", bansSlot({ hidden: true }));
        await settle();

        expect(details().hidden).toBe(true);
        expect(toggle().getAttribute("aria-expanded")).toBe("false");
    });

    it("does not hide on the word of a single replaced KPI slot and asks the watch for both", async () => {
        vi.useFakeTimers();
        const asked = staleRequests();
        renderPage({ hidden: false });
        await settle();

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true }));
        await settle();
        await vi.advanceTimersByTimeAsync(2000);

        expect(details().hidden).toBe(false);
        expect(toggle().getAttribute("aria-expanded")).toBe("true");
        expect(close().getAttribute("value")).toBe("hide:fp1");
        expect(asked.length).toBe(1);
        asked.stop();
        vi.useRealTimers();
    });

    it("lets the figure follow a swapped ban block", async () => {
        renderPage({ hidden: true });
        await settle();

        swap("zeitstrom-bans-slot", bansSlot({ hidden: false }));
        await settle();

        expect(toggle().getAttribute("aria-expanded")).toBe("true");
        expect(toggle().value).toBe("hide:fp1");
    });

    it("shows the details again when a changed list arrives in both slots", async () => {
        renderPage({ hidden: true });
        await settle();

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: false, fp: "fp2", count: 3 }));
        swap("zeitstrom-bans-slot", bansSlot({ hidden: false, fp: "fp2" }));
        await settle();

        expect(details().hidden).toBe(false);
        expect(toggle().getAttribute("aria-expanded")).toBe("true");
        expect(toggle().value).toBe("hide:fp2");
    });

    it("shows the details again when the changed KPI slot arrives before the ban block", async () => {
        renderPage({ hidden: true });
        await settle();

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: false, fp: "fp2", count: 3 }));
        await settle();

        expect(details().hidden).toBe(false);
    });

    it("discards the answer of a request during which a slot was replaced, even for the same list", async () => {
        const asked = staleRequests();
        let release;
        fetch.mockReturnValue(new Promise((resolve) => (release = resolve)));
        close().click();

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: false }));
        await settle();
        expect(details().hidden).toBe(true);

        release(answer({ hidden: true, fingerprint: "fp1" }));
        await settle();

        expect(details().hidden).toBe(false);
        expect(asked.length).toBe(1);
        asked.stop();
    });

    it("hides through slots that both come hidden at once (the watch's answer)", async () => {
        renderPage({ hidden: false });
        await settle();

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true }));
        swap("zeitstrom-bans-slot", bansSlot({ hidden: true }));
        await settle();

        expect(details().hidden).toBe(true);
        expect(toggle().getAttribute("aria-expanded")).toBe("false");
    });

    it("hides once the second slot catches up with a hidden state", async () => {
        renderPage({ hidden: false });
        await settle();

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true }));
        await settle();
        expect(details().hidden).toBe(false);
        swap("zeitstrom-bans-slot", bansSlot({ hidden: true }));
        await settle();

        expect(details().hidden).toBe(true);
    });
});

// One row per request kind x incoming change: an answer must never hide a list
// that is not provably the one the request was about.
describe("freshness of own answers", () => {
    let asked;
    let release;
    let rejectRequest;

    beforeEach(() => {
        asked = staleRequests();
        fetch.mockReturnValue(
            new Promise((resolve, reject) => {
                release = resolve;
                rejectRequest = reject;
            }),
        );
    });

    afterEach(() => {
        asked.stop();
    });

    const changes = {
        "a new list": () => {
            swap("zeitstrom-kpi-slot", kpiSlot({ fp: "fp2", count: 3 }));
            swap("zeitstrom-bans-slot", bansSlot({ fp: "fp2" }));
        },
        "the list going back to the old one": () => {
            swap("zeitstrom-kpi-slot", kpiSlot({ fp: "fp2", count: 3 }));
            swap("zeitstrom-bans-slot", bansSlot({ fp: "fp2" }));
            swap("zeitstrom-kpi-slot", kpiSlot({ fp: "fp1" }));
            swap("zeitstrom-bans-slot", bansSlot({ fp: "fp1" }));
        },
        "an empty slot": () => {
            swap("zeitstrom-kpi-slot", emptyKpiSlot());
            swap("zeitstrom-bans-slot", emptyBansSlot());
        },
        "a filter change (feed refresh with the KPI slot)": () => {
            swap("zeitstrom-kpi-slot", kpiSlot({ fp: "fp1" }));
        },
    };

    for (const [change, happen] of Object.entries(changes)) {
        it(`a late hide answer after ${change} hides nothing and asks the watch`, async () => {
            close().click();
            happen();
            await settle();

            release(answer({ hidden: true, fingerprint: "fp1" }));
            await settle();

            expect(details() ? details().hidden : false).toBe(false);
            expect(document.getElementById("zeitstrom-bans-slot").dataset.banDetailsHidden).toBe("false");
            expect(asked.length).toBeGreaterThanOrEqual(1);
        });

        it(`a failed hide request after ${change} hides nothing`, async () => {
            close().click();
            happen();
            await settle();

            rejectRequest(new Error("offline"));
            await settle();

            expect(details() ? details().hidden : false).toBe(false);
            expect(asked.length).toBeGreaterThanOrEqual(1);
        });

        it(`a late show answer after ${change} keeps the details visible`, async () => {
            renderPage({ hidden: true });
            await settle();
            toggle().click();
            happen();
            await settle();

            release(answer({ hidden: false, fingerprint: "fp1" }));
            await settle();

            expect(details() ? details().hidden : false).toBe(false);
        });
    }

    it("a failed show request does not bring the old hidden state back", async () => {
        renderPage({ hidden: true });
        await settle();
        toggle().click();

        rejectRequest(new Error("offline"));
        await settle();

        expect(details().hidden).toBe(false);
        expect(asked.length).toBe(1);
    });

    it("an answer without any replaced slot still applies (unchanged path)", async () => {
        close().click();

        release(answer({ hidden: true, fingerprint: "fp1" }));
        await settle();

        expect(details().hidden).toBe(true);
        expect(asked.length).toBe(0);
    });
});

describe("slots that disagree", () => {
    let asked;

    beforeEach(() => {
        vi.useFakeTimers();
        asked = staleRequests();
    });

    afterEach(() => {
        asked.stop();
        vi.useRealTimers();
    });

    it("keeps a newer ban block visible when an older KPI slot arrives hidden", async () => {
        renderPage({ hidden: true });
        await settle();
        swap("zeitstrom-bans-slot", bansSlot({ hidden: false, fp: "fp2" }));
        await settle();

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true, fp: "fp1" }));
        await settle();

        expect(details().hidden).toBe(false);
        expect(toggle().getAttribute("aria-expanded")).toBe("true");
    });

    it("asks the live watch, not the server directly, when they still disagree after a short while", async () => {
        renderPage({ hidden: true });
        await settle();
        swap("zeitstrom-bans-slot", bansSlot({ hidden: false, fp: "fp2" }));
        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true, fp: "fp1", count: 2 }));
        await settle();

        await vi.advanceTimersByTimeAsync(2000);

        expect(asked.length).toBe(1);
        expect(fetch).not.toHaveBeenCalled();
        expect(details().hidden).toBe(false);
    });

    it("does not ask when the other slot catches up within the grace period", async () => {
        renderPage({ hidden: true });
        await settle();

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: false, fp: "fp2", count: 3 }));
        await settle();
        expect(details().hidden).toBe(false);
        swap("zeitstrom-bans-slot", bansSlot({ hidden: false, fp: "fp2" }));
        await settle();
        await vi.advanceTimersByTimeAsync(5000);

        expect(asked.length).toBe(0);
    });

    it("gives up after two rounds and leaves the details visible", async () => {
        renderPage({ hidden: true });
        await settle();
        swap("zeitstrom-bans-slot", bansSlot({ hidden: false, fp: "fp2" }));
        await settle();
        for (let round = 0; round < 4; round++) {
            await vi.advanceTimersByTimeAsync(2000);
            // The watch answers again with the same disagreeing pair.
            swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true, fp: "fp1" }));
            await settle();
        }
        await vi.advanceTimersByTimeAsync(5000);

        expect(asked.length).toBe(2);
        expect(details().hidden).toBe(false);
    });
});

// The refresh runs through the live watch: its snapshot and generation checks
// drop every answer that a newer trigger has overtaken (the late empty pair of the
// review), and the next cycle sends no validator for the two slots.
describe("refresh through the live watch", () => {
    function response({ status = 200, json = null, text = "", headers = {} }) {
        const normalized = new Map(Object.entries(headers).map(([name, value]) => [name.toLowerCase(), value]));
        return {
            status,
            ok: status >= 200 && status < 300,
            redirected: false,
            url: "",
            headers: { get: (name) => normalized.get(name.toLowerCase()) || null },
            json: vi.fn(async () => json),
            text: vi.fn(async () => text),
        };
    }

    function watchAnswer(version) {
        return response({
            json: { components: ["kpi", "bans"], validators: { kpi: `"kpi-${version}"`, bans: `"bans-${version}"` } },
            headers: { "Content-Type": "application/json", ETag: `"page-${version}"` },
        });
    }

    function slotAnswer(html, validator) {
        return response({ text: html, headers: { "Content-Type": "text/html", ETag: validator } });
    }

    function deferredValue() {
        let resolve;
        const promise = new Promise((yes) => (resolve = yes));
        return { promise, resolve };
    }

    const triggers = {
        "another refresh request": () => window.dispatchEvent(new CustomEvent("zeitstrom:ban-slots-stale")),
        "a filter change": () =>
            window.dispatchEvent(new CustomEvent("zeitstrom:filter-changed", { detail: { name: "type" } })),
    };

    for (const [trigger, fire] of Object.entries(triggers)) {
        it(`drops a late empty pair that ${trigger} has overtaken`, async () => {
            renderPage({ hidden: true });
            await settle();
            const lateKpi = deferredValue();
            const lateBans = deferredValue();
            let cycle = 0;
            const fetchMock = vi.fn((url) => {
                const path = new URL(url, "https://example.test").pathname;
                if (path === "/w/") {
                    cycle += 1;
                    return Promise.resolve(watchAnswer(cycle));
                }
                if (cycle === 1) {
                    return path.includes("/kpi/") ? lateKpi.promise : lateBans.promise;
                }
                return Promise.resolve(
                    path.includes("/kpi/")
                        ? slotAnswer(kpiSlot({ fp: "fp2", count: 3 }), `"kpi-${cycle}"`)
                        : slotAnswer(bansSlot({ fp: "fp2" }), `"bans-${cycle}"`),
                );
            });
            const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
                fetch: fetchMock,
                navigate: vi.fn(),
            });

            watcher.start();
            const first = watcher.checkNow();
            // The old cycle has asked for both slots; its answers are held back.
            await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
            fire();
            await watcher.checkNow();
            await settle();
            expect(document.getElementById("zeitstrom-kpi-slot").dataset.banFingerprint).toBe("fp2");

            lateKpi.resolve(slotAnswer(emptyKpiSlot(), '"kpi-1"'));
            lateBans.resolve(slotAnswer(emptyBansSlot(), '"bans-1"'));
            await first;
            await settle();

            expect(document.getElementById("zeitstrom-kpi-slot").dataset.banFingerprint).toBe("fp2");
            expect(document.getElementById("zeitstrom-bans-slot").dataset.fingerprint).toBe("fp2");
            expect(details().hidden).toBe(false);
            watcher.stop();
        });
    }

    it("sends no validator for the two slots after a refresh request, so the server answers 200", async () => {
        const sent = [];
        const fetchMock = vi.fn((url, init) => {
            const path = new URL(url, "https://example.test").pathname;
            if (path === "/w/") {
                sent.push(JSON.parse((init.headers && init.headers["X-Zeitstrom-Validators"]) || "{}"));
                return Promise.resolve(watchAnswer(sent.length));
            }
            return Promise.resolve(
                path.includes("/kpi/")
                    ? slotAnswer(kpiSlot(), `"kpi-${sent.length}"`)
                    : slotAnswer(bansSlot(), `"bans-${sent.length}"`),
            );
        });
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            navigate: vi.fn(),
        });
        watcher.start();
        await watcher.checkNow();
        await settle();
        await watcher.checkNow();
        await settle();
        expect(sent.at(-1).kpi).toBe('"kpi-1"');
        expect(sent.at(-1).bans).toBe('"bans-1"');

        window.dispatchEvent(new CustomEvent("zeitstrom:ban-slots-stale"));
        await watcher.checkNow();
        await settle();

        const last = sent.at(-1);
        expect(last.kpi).toBeUndefined();
        expect(last.bans).toBeUndefined();
        watcher.stop();
    });
});

describe("the handover and the missing form", () => {
    it("has no close button and nothing to do in the handover", async () => {
        document.body.innerHTML = `<main id="zeitstrom-live-watch">${bansSlot({ handover: true })}</main>`;

        document.getElementById("zeitstrom-ban-details").click();
        await settle();

        expect(close()).toBeNull();
        expect(fetch).not.toHaveBeenCalled();
        expect(details().hidden).toBe(false);
    });
});

describe("live watch swaps keep the focus", () => {
    function response({ json = null, text = "", headers = {} }) {
        const normalized = new Map(Object.entries(headers).map(([name, value]) => [name.toLowerCase(), value]));
        return {
            status: 200,
            ok: true,
            redirected: false,
            url: "",
            headers: { get: (name) => normalized.get(name.toLowerCase()) || null },
            json: vi.fn(async () => json),
            text: vi.fn(async () => text),
        };
    }

    function watchResponse(component, version) {
        return response({
            json: { components: [component], validators: { [component]: version } },
            headers: { "Content-Type": "application/json", ETag: '"page-v1"' },
        });
    }

    function htmlResponse(html, version) {
        return response({ text: html, headers: { "Content-Type": "text/html", ETag: version } });
    }

    async function watchSwap(component, slotHtml) {
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(watchResponse(component, '"fresh"'))
            .mockResolvedValueOnce(htmlResponse(slotHtml, '"fresh"'));
        const watcher = window.createZeitstromLiveWatch(document.getElementById("zeitstrom-live-watch"), {
            fetch: fetchMock,
            navigate: vi.fn(),
        });
        await watcher.checkNow();
        await settle();
        watcher.stop();
    }

    it("keeps the focus on the figure when the KPI slot is replaced", async () => {
        const before = toggle();
        before.focus();

        await watchSwap("kpi", kpiSlot({ count: 2 }));

        expect(toggle()).not.toBe(before);
        expect(document.activeElement).toBe(toggle());
    });

    it("keeps the focus on the close button when the ban slot is replaced", async () => {
        const before = close();
        before.focus();

        await watchSwap("bans", bansSlot({}));

        expect(close()).not.toBe(before);
        expect(document.activeElement).toBe(close());
    });

    it("lets the hidden state survive a KPI swap together with a ban swap", async () => {
        renderPage({ hidden: true });
        await settle();

        await watchSwap("kpi", kpiSlot({ hidden: true }));
        await watchSwap("bans", bansSlot({ hidden: true }));

        expect(details().hidden).toBe(true);
        expect(toggle().getAttribute("aria-expanded")).toBe("false");
    });

    it("shows the details again when the live swap brings a changed list", async () => {
        renderPage({ hidden: true });
        await settle();

        await watchSwap("kpi", kpiSlot({ hidden: false, fp: "fp2", count: 3 }));
        await watchSwap("bans", bansSlot({ hidden: false, fp: "fp2" }));

        expect(details().hidden).toBe(false);
        expect(toggle().getAttribute("aria-expanded")).toBe("true");
    });

    it("does not scroll the page on a swap while the figure holds the focus", async () => {
        const scrollTo = vi.fn();
        window.scrollTo = scrollTo;
        toggle().focus();

        await watchSwap("kpi", kpiSlot({ count: 2 }));

        expect(scrollTo).not.toHaveBeenCalled();
    });
});
