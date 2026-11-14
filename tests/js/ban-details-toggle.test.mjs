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

    it("lets the ban block follow a swapped KPI slot", async () => {
        renderPage({ hidden: false });
        await settle();

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true }));
        await settle();

        expect(details().hidden).toBe(true);
        expect(close().getAttribute("value")).toBe("hide:fp1");
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

    it("does not let a swap disturb a request in flight", async () => {
        let release;
        fetch.mockReturnValue(new Promise((resolve) => (release = resolve)));
        close().click();

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: false }));
        await settle();
        expect(details().hidden).toBe(true);

        release(answer({ hidden: true, fingerprint: "fp1" }));
        await settle();
        expect(details().hidden).toBe(true);
    });

    it("moves the focus to the figure when the block is hidden under the close button", async () => {
        renderPage({ hidden: false });
        await settle();
        close().focus();

        swap("zeitstrom-kpi-slot", kpiSlot({ hidden: true }));
        await settle();

        expect(document.activeElement).toBe(toggle());
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
