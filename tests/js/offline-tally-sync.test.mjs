// #1912: reale Script-Reihenfolge aus base.html: extra_js/offline-tally.js
// laedt VOR sync-orchestrator.js. Der zentrale Abschluss muss im selben Tab
// trotzdem genau einmal aus dem bereits revalidierten Snapshot rendern.
import { beforeAll, expect, it, vi } from "vitest";

const TYPE_ID = "11111111-1111-4111-8111-111111111111";

let snapshot = {
    shift_date: "2026-08-16",
    time_filter: null,
    window_start: "2026-08-16T00:00:00Z",
    window_end: "2026-08-16T23:59:59Z",
    rows: [
        {
            document_type_id: TYPE_ID,
            document_type_name: "Kontakt",
            mode: "direct",
            fields: [],
            confirmed_count: 12,
        },
    ],
};
let pending = 1;
const replayQueue = vi.fn(async () => {
    pending = 0;
});
const revalidateCachedFacility = vi.fn(async () => {
    snapshot = {
        ...snapshot,
        rows: [{ ...snapshot.rows[0], confirmed_count: 13 }],
    };
    return "refreshed";
});
const getOfflineFacility = vi.fn(async () => ({ tally: snapshot }));
let publicRequestSync;
const reconciled = vi.fn();

beforeAll(async () => {
    const readyState = vi.spyOn(document, "readyState", "get").mockReturnValue("loading");
    document.body.innerHTML = `<section id="tally-panel"><div data-testid="tally-row-${TYPE_ID}"><span data-testid="tally-count-${TYPE_ID}" data-count-label-singular="1 Kontakt für Kontakt" data-count-label-plural="__count__ Kontakte für Kontakt" data-pending-aria-singular="+1 wird für Kontakt gesendet" data-pending-aria-plural="+__count__ werden für Kontakt gesendet" aria-label="12 Kontakte für Kontakt">12</span></div></section>`;
    delete window.syncOrchestrator;
    globalThis.Alpine = { data: vi.fn() };
    window.crypto_session = { ready: async () => {}, hasSessionKey: () => true };
    window.offlineQueue = { replayQueue };
    window.offlineEdit = { replayAllModifiedEvents: vi.fn(async () => {}) };
    window.offlineStore = {
        getOfflineFacility,
        listTallyQueueState: async () => ({
            byDocumentType: { [TYPE_ID]: { pending, blocked: 0 } },
            pending,
            blocked: 0,
        }),
        purgeExpired: vi.fn(async () => {}),
        revalidateCachedClients: vi.fn(async () => {}),
        revalidateCachedFacility,
        hasReplayableWork: vi.fn(async () => false),
    };
    class NoEchoBroadcastChannel {
        postMessage() {}
        close() {}
    }
    globalThis.BroadcastChannel = NoEchoBroadcastChannel;

    // Exakt die produktive Reihenfolge: Feature-Script zuerst.
    await import("../../src/static/js/offline-tally.js");
    expect(window.syncOrchestrator).toBeUndefined();
    await window.offlineTallyUi.refreshOnlinePanel();
    expect(document.querySelector("[data-offline-tally-status]").textContent).toBe(
        "+1 wird gesendet"
    );
    // base.html laedt den zentralen Orchestrator erst danach.
    await import("../../src/static/js/sync-orchestrator.js");
    publicRequestSync = vi.spyOn(window.syncOrchestrator, "requestSync");
    window.addEventListener("offline-tally-reconciled", reconciled);
    readyState.mockReturnValue("interactive");
    document.dispatchEvent(new Event("DOMContentLoaded"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    getOfflineFacility.mockClear();
});

it("reconciled same-tab nach genau einem zentralen Sync und einer Bundle-Revalidation", async () => {
    window.dispatchEvent(new Event("online"));

    await vi.waitFor(() => {
        expect(document.querySelector(`[data-testid="tally-count-${TYPE_ID}"]`).textContent).toBe("13");
        expect(document.querySelector(`[data-testid="tally-count-${TYPE_ID}"]`).getAttribute("aria-label")).toBe(
            "13 Kontakte für Kontakt"
        );
        expect(document.querySelector("[data-offline-tally-status]")).toBeNull();
    });
    expect(replayQueue).toHaveBeenCalledTimes(1);
    expect(revalidateCachedFacility).toHaveBeenCalledTimes(1);
    expect(getOfflineFacility).toHaveBeenCalledTimes(1);
    expect(publicRequestSync).not.toHaveBeenCalled();
    expect(reconciled).toHaveBeenCalledTimes(1);
});

it("ueberschreibt bei fehlgeschlagener Revalidation keinen frischeren DOM-Stand", async () => {
    snapshot = {
        ...snapshot,
        rows: [{ ...snapshot.rows[0], confirmed_count: 11 }],
    };
    document.body.innerHTML = `<section id="tally-panel"><div data-testid="tally-row-${TYPE_ID}"><span data-testid="tally-count-${TYPE_ID}" data-count-label-singular="1 Kontakt für Kontakt" data-count-label-plural="__count__ Kontakte für Kontakt" data-pending-aria-singular="+1 wird für Kontakt gesendet" data-pending-aria-plural="+__count__ werden für Kontakt gesendet" aria-label="12 Kontakte für Kontakt">12</span></div></section>`;
    const callsBefore = revalidateCachedFacility.mock.calls.length;

    window.dispatchEvent(
        new CustomEvent("offline-sync-finished", {
            detail: { facilityRevalidation: "error" },
        })
    );

    await vi.waitFor(() => {
        expect(document.querySelector(`[data-testid="tally-count-${TYPE_ID}"]`).textContent).toBe("12");
        expect(document.querySelector("[data-offline-tally-status]")).toBeNull();
    });
    expect(revalidateCachedFacility).toHaveBeenCalledTimes(callsBefore);
});
