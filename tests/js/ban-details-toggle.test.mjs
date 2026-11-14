// Hiding the ban details of the Zeitstrom: the figure in the KPI row and the close
// button of the detail block. The details are visible unless every condition of
// shouldHideDetails holds; each describe block below is one part of the state
// table in the lane report (event x state => decision).
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

// The KPI row without a ban figure (no active ban when it was rendered).
function emptyKpiSlot() {
    return '<div id="zeitstrom-kpi-slot" data-ban-fingerprint="" data-ban-details-hidden="false"><ul></ul></div>';
}

function emptyBansSlot() {
    return '<div id="zeitstrom-bans-slot" data-fingerprint="" data-ban-details-hidden="false"></div>';
}

// Records every repair request of the toggle script to the live watch.
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

// A request whose answer the test hands out (or withholds) itself.
function heldRequest() {
    const held = {};
    held.promise = new Promise((resolve, reject) => {
        held.release = resolve;
        held.fail = reject;
    });
    return held;
}

function sentChoices() {
    return fetch.mock.calls.map((call) => call[1].body.get("ban_details"));
}

const details = () => document.getElementById("zeitstrom-ban-details");
const toggle = () => document.getElementById("zeitstrom-ban-details-toggle");
const close = () => document.getElementById("zeitstrom-ban-details-close");
const isHidden = () => !!details() && details().hidden;

beforeEach(async () => {
    globalThis.fetch = vi.fn();
    renderPage();
    await settle();
});

afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
});

describe("shouldHideDetails (the decision itself)", () => {
    const all = {
        pending: false,
        kpiList: "fp1",
        bansList: "fp1",
        figurePresent: true,
        confirmedHidden: true,
        confirmedList: "fp1",
    };

    it("hides only when every condition holds", () => {
        expect(window.__banDetailsShouldHide(all)).toBe(true);
    });

    it.each([
        ["an own request is open", { pending: true }],
        ["the slots carry different lists", { kpiList: "fp2" }],
        ["there is no KPI slot statement", { kpiList: null }],
        ["the list is empty", { kpiList: "", bansList: "" }],
        ["the ban figure is missing", { figurePresent: false }],
        ["the confirmed choice is shown", { confirmedHidden: false }],
        ["the confirmed choice was for another list", { confirmedList: "fp2" }],
    ])("shows the details when %s", (_name, change) => {
        expect(window.__banDetailsShouldHide({ ...all, ...change })).toBe(false);
    });
});

describe("own requests", () => {
    it("keeps the details visible while the hide request is open, then hides and moves the focus", async () => {
        const held = heldRequest();
        fetch.mockReturnValue(held.promise);
        close().focus();

        close().click();
        await settle();
        expect(isHidden()).toBe(false);
        expect(document.activeElement).toBe(close());

        held.release(answer({ hidden: true, fingerprint: "fp1" }));
        await settle();

        expect(isHidden()).toBe(true);
        expect(toggle().getAttribute("aria-expanded")).toBe("false");
        expect(toggle().value).toBe("show");
        expect(document.activeElement).toBe(toggle());
        const [url, init] = fetch.mock.calls[0];
        expect(url).toContain("/zeitstrom/ban-details/");
        expect(init.method).toBe("POST");
        expect(init.body.get("ban_details")).toBe("hide:fp1");
        expect(init.body.get("csrfmiddlewaretoken")).toBe("token-1");
        expect(init.headers.Accept).toBe("application/json");
    });

    it("shows at once on the figure and tells the server", async () => {
        renderPage({ hidden: true });
        await settle();
        expect(isHidden()).toBe(true);
        const held = heldRequest();
        fetch.mockReturnValue(held.promise);

        toggle().click();
        await settle();
        expect(isHidden()).toBe(false);

        held.release(answer({ hidden: false, fingerprint: "fp1" }));
        await settle();

        expect(isHidden()).toBe(false);
        expect(toggle().getAttribute("aria-expanded")).toBe("true");
        expect(toggle().value).toBe("hide:fp1");
        expect(sentChoices()).toEqual(["show"]);
    });

    it("hides with the fingerprint of the list on screen, not a stale button value", async () => {
        fetch.mockResolvedValue(answer({ hidden: true, fingerprint: "fp1" }));
        toggle().value = "hide:old";

        toggle().click();
        await settle();

        expect(sentChoices()[0]).toBe("hide:fp1");
    });

    it("stays visible when the server did not store the choice (list changed there)", async () => {
        fetch.mockResolvedValue(answer({ hidden: false, fingerprint: "fp2" }));

        close().click();
        await settle();

        expect(isHidden()).toBe(false);
        expect(sentChoices()).toEqual(["hide:fp1"]);
    });

    it("stays visible on a failed hide and tells the server to forget it", async () => {
        fetch.mockRejectedValueOnce(new Error("offline")).mockResolvedValue(answer({ hidden: false, fingerprint: "fp1" }));

        close().click();
        await settle();

        expect(isHidden()).toBe(false);
        expect(sentChoices()).toEqual(["hide:fp1", "show"]);
    });

    it("stays visible when the server refuses", async () => {
        fetch.mockResolvedValueOnce(answer({}, false)).mockResolvedValue(answer({ hidden: false, fingerprint: "fp1" }));

        close().click();
        await settle();

        expect(isHidden()).toBe(false);
        expect(sentChoices()).toEqual(["hide:fp1", "show"]);
    });

    it("stays visible on a failed show and keeps no old hidden state", async () => {
        renderPage({ hidden: true });
        await settle();
        fetch.mockRejectedValue(new Error("offline"));

        toggle().click();
        await settle();
        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true }));
        swap("zeitstrom-bans-slot", bansSlot({ hidden: true }));
        await settle();

        expect(isHidden()).toBe(false);
    });

    it("sends one request while another is in flight", async () => {
        const held = heldRequest();
        fetch.mockReturnValue(held.promise);

        close().click();
        toggle().click();
        held.release(answer({ hidden: true, fingerprint: "fp1" }));
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

// Events while an own request is open: every uncertainty shows the details at
// once, the answer is used only if nothing was replaced meanwhile.
describe("while a request is open", () => {
    let held;

    beforeEach(() => {
        held = heldRequest();
        fetch.mockReturnValue(held.promise);
    });

    it("shows the details at once when an old KPI slot without the figure arrives (review run 3)", async () => {
        close().click();
        await settle();

        swap("zeitstrom-kpi-slot", emptyKpiSlot());
        await settle();

        // Still open: the notice must not depend on the answer.
        expect(isHidden()).toBe(false);
        expect(details().textContent).toContain("Hausverbot");
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
        "an old hidden pair of the same list": () => {
            swap("zeitstrom-kpi-slot", kpiSlot({ fp: "fp1", hidden: true }));
            swap("zeitstrom-bans-slot", bansSlot({ fp: "fp1", hidden: true }));
        },
    };

    for (const [change, happen] of Object.entries(changes)) {
        it(`a late hide answer after ${change} hides nothing and is taken back at the server`, async () => {
            close().click();
            happen();
            await settle();
            expect(isHidden()).toBe(false);

            held.release(answer({ hidden: true, fingerprint: "fp1" }));
            await settle();

            expect(isHidden()).toBe(false);
            expect(sentChoices()).toEqual(["hide:fp1", "show"]);
        });

        it(`a failed hide request after ${change} hides nothing and is taken back`, async () => {
            close().click();
            happen();
            await settle();

            held.fail(new Error("offline"));
            await settle();

            expect(isHidden()).toBe(false);
            expect(sentChoices()[1]).toBe("show");
        });

        it(`a late show answer after ${change} keeps the details visible`, async () => {
            renderPage({ hidden: true });
            await settle();
            toggle().click();
            happen();
            await settle();

            held.release(answer({ hidden: false, fingerprint: "fp1" }));
            await settle();

            expect(isHidden()).toBe(false);
        });
    }

    it("an answer without any replaced slot still applies (control)", async () => {
        close().click();

        held.release(answer({ hidden: true, fingerprint: "fp1" }));
        await settle();

        expect(isHidden()).toBe(true);
        expect(sentChoices()).toEqual(["hide:fp1"]);
    });
});

// Slots that arrive after a confirmed choice: they may end a confirmed "hidden",
// never start one.
describe("slots after a confirmed choice", () => {
    it("keeps the hidden state when both slots come again with the same list and choice", async () => {
        renderPage({ hidden: true });
        await settle();

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true }));
        swap("zeitstrom-bans-slot", bansSlot({ hidden: true }));
        await settle();

        expect(isHidden()).toBe(true);
        expect(toggle().getAttribute("aria-expanded")).toBe("false");
    });

    it("keeps the hidden state when only the KPI slot comes again with the same list and choice", async () => {
        renderPage({ hidden: true });
        await settle();

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true, count: 2 }));
        await settle();

        expect(isHidden()).toBe(true);
    });

    it("shows the details when a slot says shown for the list, and a later hidden pair does not hide again", async () => {
        renderPage({ hidden: true });
        await settle();

        swap("zeitstrom-bans-slot", bansSlot({ hidden: false }));
        await settle();
        expect(isHidden()).toBe(false);

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true }));
        swap("zeitstrom-bans-slot", bansSlot({ hidden: true }));
        await settle();
        expect(isHidden()).toBe(false);
    });

    it("ends the choice on a new list and does not hide when the old list comes back", async () => {
        renderPage({ hidden: true });
        await settle();

        swap("zeitstrom-kpi-slot", kpiSlot({ fp: "fp2", count: 3 }));
        swap("zeitstrom-bans-slot", bansSlot({ fp: "fp2" }));
        await settle();
        expect(isHidden()).toBe(false);
        expect(toggle().value).toBe("hide:fp2");

        swap("zeitstrom-kpi-slot", kpiSlot({ fp: "fp1", hidden: true }));
        swap("zeitstrom-bans-slot", bansSlot({ fp: "fp1", hidden: true }));
        await settle();
        expect(isHidden()).toBe(false);
    });

    it("shows the details when the changed KPI slot arrives before the ban block", async () => {
        renderPage({ hidden: true });
        await settle();

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: false, fp: "fp2", count: 3 }));
        await settle();

        expect(isHidden()).toBe(false);
    });

    it("keeps a newer ban block visible when an older KPI slot arrives hidden", async () => {
        renderPage({ hidden: true });
        await settle();
        swap("zeitstrom-bans-slot", bansSlot({ hidden: false, fp: "fp2" }));
        await settle();

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true, fp: "fp1" }));
        await settle();

        expect(isHidden()).toBe(false);
        expect(toggle().getAttribute("aria-expanded")).toBe("true");
    });

    it("shows the details when the KPI slot loses the figure", async () => {
        renderPage({ hidden: true });
        await settle();

        swap("zeitstrom-kpi-slot", emptyKpiSlot());
        await settle();

        expect(isHidden()).toBe(false);
    });

    it("never hides on a hidden pair that arrives on a page whose choice is shown", async () => {
        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true }));
        swap("zeitstrom-bans-slot", bansSlot({ hidden: true }));
        await settle();

        expect(isHidden()).toBe(false);
    });

    it("an old hidden pair after a confirmed show does not hide again and gives the close button its focus back (review run 4)", async () => {
        renderPage({ hidden: true });
        await settle();
        fetch.mockResolvedValue(answer({ hidden: false, fingerprint: "fp1" }));
        toggle().click();
        await settle();
        expect(isHidden()).toBe(false);
        close().focus();

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true }));
        swap("zeitstrom-bans-slot", bansSlot({ hidden: true }));
        await settle();

        expect(isHidden()).toBe(false);
        expect(document.activeElement).toBe(close());
    });
});

describe("repair requests", () => {
    let asked;

    beforeEach(() => {
        vi.useFakeTimers();
        asked = staleRequests();
    });

    afterEach(() => {
        asked.stop();
    });

    it("asks the live watch, not the server directly, when the lists still differ after a short while", async () => {
        renderPage({ hidden: true });
        await settle();
        swap("zeitstrom-bans-slot", bansSlot({ hidden: false, fp: "fp2" }));
        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true, fp: "fp1", count: 2 }));
        await settle();

        await vi.advanceTimersByTimeAsync(2000);

        expect(asked.length).toBe(1);
        expect(fetch).not.toHaveBeenCalled();
        expect(isHidden()).toBe(false);
    });

    it("does not ask when the other slot catches up within the grace period", async () => {
        renderPage({ hidden: true });
        await settle();

        swap("zeitstrom-kpi-slot", kpiSlot({ fp: "fp2", count: 3 }));
        await settle();
        swap("zeitstrom-bans-slot", bansSlot({ fp: "fp2" }));
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
            swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true, fp: "fp1" }));
            await settle();
        }
        await vi.advanceTimersByTimeAsync(5000);

        expect(asked.length).toBe(2);
        expect(isHidden()).toBe(false);
    });
});

describe("the handover and the missing form", () => {
    it("has no close button and nothing to do in the handover", async () => {
        document.body.innerHTML = `<main id="zeitstrom-live-watch">${bansSlot({ handover: true })}</main>`;
        await settle();

        document.getElementById("zeitstrom-ban-details").click();
        await settle();

        expect(close()).toBeNull();
        expect(fetch).not.toHaveBeenCalled();
        expect(isHidden()).toBe(false);
    });
});

// The repair runs through the live watch: it waits for a running cycle instead of
// aborting it, and the next cycle sends no validator for the two slots.
describe("repair through the live watch", () => {
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

    it("lets a running cycle finish, then brings the newer list (review run 2, watch design)", async () => {
        renderPage({ hidden: true });
        await settle();
        const firstKpi = deferredValue();
        const firstBans = deferredValue();
        const sent = [];
        let cycle = 0;
        const fetchMock = vi.fn((url, init) => {
            const path = new URL(url, "https://example.test").pathname;
            if (path === "/w/") {
                cycle += 1;
                sent.push(JSON.parse((init.headers && init.headers["X-Zeitstrom-Validators"]) || "{}"));
                return Promise.resolve(watchAnswer(cycle));
            }
            if (cycle === 1) {
                return path.includes("/kpi/") ? firstKpi.promise : firstBans.promise;
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
        await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
        window.dispatchEvent(new CustomEvent("zeitstrom:ban-slots-stale"));
        await settle();
        expect(fetchMock).toHaveBeenCalledTimes(3);

        firstKpi.resolve(slotAnswer(emptyKpiSlot(), '"kpi-1"'));
        firstBans.resolve(slotAnswer(emptyBansSlot(), '"bans-1"'));
        await vi.waitFor(() =>
            expect(document.getElementById("zeitstrom-kpi-slot").dataset.banFingerprint).toBe("fp2"),
        );

        expect(document.getElementById("zeitstrom-bans-slot").dataset.fingerprint).toBe("fp2");
        expect(isHidden()).toBe(false);
        expect(sent.at(-1).kpi).toBeUndefined();
        expect(sent.at(-1).bans).toBeUndefined();
        watcher.stop();
    });

    it("drops a late empty pair that a filter change has overtaken (control: also on the old code)", async () => {
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
        await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
        window.dispatchEvent(new CustomEvent("zeitstrom:filter-changed", { detail: { name: "type" } }));
        await watcher.checkNow();
        await settle();
        expect(document.getElementById("zeitstrom-kpi-slot").dataset.banFingerprint).toBe("fp2");

        lateKpi.resolve(slotAnswer(emptyKpiSlot(), '"kpi-1"'));
        lateBans.resolve(slotAnswer(emptyBansSlot(), '"bans-1"'));
        await first;
        await settle();

        expect(document.getElementById("zeitstrom-kpi-slot").dataset.banFingerprint).toBe("fp2");
        expect(document.getElementById("zeitstrom-bans-slot").dataset.fingerprint).toBe("fp2");
        watcher.stop();
    });

    it("sends no validator for the two slots after a repair request, so the server answers 200", async () => {
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

        expect(sent.at(-1).kpi).toBeUndefined();
        expect(sent.at(-1).bans).toBeUndefined();
        watcher.stop();
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

        expect(isHidden()).toBe(true);
        expect(toggle().getAttribute("aria-expanded")).toBe("false");
    });

    it("shows the details again when the live swap brings a changed list", async () => {
        renderPage({ hidden: true });
        await settle();

        await watchSwap("kpi", kpiSlot({ hidden: false, fp: "fp2", count: 3 }));
        await watchSwap("bans", bansSlot({ hidden: false, fp: "fp2" }));

        expect(isHidden()).toBe(false);
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
