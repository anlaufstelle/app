// Refs #1479 (TST-01): CSRF-Refresh der Offline-Replay-Konsumenten.
// ``csrfUtils.refresh()`` holt den Token vom dedizierten JSON-Endpoint
// (statt fragilem HTML-Scrape, der in #1330/#1332 die Stale-CSRF-403-Bugs
// verursachte) und spiegelt ihn ins <meta name="csrf-token">.
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from "vitest";

let csrfUtils;

beforeAll(async () => {
    await import("../../src/static/js/csrf-utils.js");
    csrfUtils = window.csrfUtils;
});

beforeEach(() => {
    document.head.innerHTML = '<meta name="csrf-token" content="OLD-TOKEN">';
});

afterEach(() => {
    vi.restoreAllMocks();
    delete global.fetch;
});

function meta() {
    return document.querySelector('meta[name="csrf-token"]').getAttribute("content");
}

describe("csrfUtils.fromMeta", () => {
    it("liest den Token aus dem Meta-Tag", () => {
        expect(csrfUtils.fromMeta()).toBe("OLD-TOKEN");
    });
});

describe("csrfUtils.refresh", () => {
    it("liefert den frischen Token und aktualisiert das Meta-Tag", async () => {
        global.fetch = vi.fn(async () => ({
            ok: true,
            json: async () => ({ csrftoken: "FRESH-TOKEN" }),
        }));
        const token = await csrfUtils.refresh();
        expect(token).toBe("FRESH-TOKEN");
        expect(meta()).toBe("FRESH-TOKEN");
        expect(global.fetch).toHaveBeenCalledWith(
            "/api/v1/offline/csrf/",
            expect.objectContaining({ method: "GET", credentials: "same-origin" })
        );
    });

    it("gibt null zurueck bei !ok und laesst das Meta-Tag unveraendert", async () => {
        global.fetch = vi.fn(async () => ({ ok: false, json: async () => ({}) }));
        const token = await csrfUtils.refresh();
        expect(token).toBeNull();
        expect(meta()).toBe("OLD-TOKEN");
    });

    it("gibt null zurueck bei Netzwerkfehler (kein stale-Token-Retry)", async () => {
        global.fetch = vi.fn(async () => {
            throw new Error("network down");
        });
        const token = await csrfUtils.refresh();
        expect(token).toBeNull();
        expect(meta()).toBe("OLD-TOKEN");
    });
});
