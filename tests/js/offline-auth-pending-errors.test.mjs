// Refs #2626: the "login required" hint and the locally cached client list
// must not hide unreadable local storage. A failed read is logged and reported
// as an UNKNOWN count; a partial sum is never shown as an exact number.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const origin = window.location.origin;
const unhandled = [];
const onUnhandled = (reason) => unhandled.push(reason);
const signals = [];
const unknownEvents = [];
const recordSignal = (event) => signals.push(event.detail);
const recordUnknown = (event) => unknownEvents.push(event.detail);
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
let store;

function mountHint(withUnknownText) {
    document.body.innerHTML =
        '<div data-offline-auth-pending hidden data-auth-pending-text="Login needed: {count} changes stay local." ' +
        'data-auth-pending-password-text="Password change needed: {count} changes unsent."' +
        (withUnknownText
            ? ' data-auth-pending-unknown-text="Login needed: number of changes unclear."' +
              ' data-auth-pending-password-unknown-text="Password change needed: number unclear."'
            : "") +
        "><span data-offline-auth-pending-text></span></div>";
    return document.querySelector("[data-offline-auth-pending-text]");
}

beforeAll(async () => {
    Object.defineProperty(window.navigator, "onLine", { configurable: true, value: true });
    window.crypto_session = { awaitReady: async () => {}, hasSessionKey: () => true };
    window.csrfUtils = { fromMeta: () => "csrf-token", refresh: async () => "fresh-csrf-token" };
    store = {
        isRevocationPending: async () => false,
        countUnsyncedEvents: vi.fn(async () => 2),
        countConflictEvents: vi.fn(async () => 0),
        countDeadEvents: vi.fn(async () => 0),
        countQueueByStatus: vi.fn(async () => ({ total: 1, pending: 1, blocked: 0 })),
        listDecrypted: vi.fn(async () => [{
            id: 1,
            url: `${origin}/partials/workitems/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1/status/`,
            attempts: 0,
            retryAfter: 0,
            idempotencyKey: "idem-1",
            data: { method: "POST", body: "status=done", headers: { "hx-request": "true" } },
        }]),
        deleteRow: vi.fn(async () => {}),
        putEncrypted: vi.fn(async () => {}),
        coalescePendingQueueByUrl: vi.fn(async () => false),
        count: vi.fn(async () => 1),
        listOfflineClients: vi.fn(async () => [{ pk: "c1", lastSynced: "t" }]),
        countOfflineClients: vi.fn(async () => 1),
    };
    window.offlineStore = store;
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/offline-queue.js");
    await import("../../src/static/js/offline-edit.js");
    await import("../../src/static/js/offline-client.js");
});

beforeEach(() => {
    unhandled.length = 0;
    signals.length = 0;
    unknownEvents.length = 0;
    process.on("unhandledRejection", onUnhandled);
    window.addEventListener("offline-auth-pending", recordSignal);
    window.addEventListener("offline-count-unknown", recordUnknown);
    vi.spyOn(console, "warn").mockImplementation(() => {});
    store.countUnsyncedEvents.mockResolvedValue(2);
    store.countQueueByStatus.mockResolvedValue({ total: 1, pending: 1, blocked: 0 });
    store.listOfflineClients.mockResolvedValue([{ pk: "c1", lastSynced: "t" }]);
});

afterEach(() => {
    process.off("unhandledRejection", onUnhandled);
    window.removeEventListener("offline-auth-pending", recordSignal);
    window.removeEventListener("offline-count-unknown", recordUnknown);
    vi.restoreAllMocks();
    document.body.innerHTML = "";
});

describe("offline-edit: auth-pending hint with unreadable counters", () => {
    it("keeps the exact number when every source is readable", async () => {
        const text = mountHint(true);
        await window.offlineEdit.signalAuthPending(true, "login");
        expect(signals).toEqual([{ pending: true, count: 3, kind: "login" }]);
        expect(text.textContent).toBe("Login needed: 3 changes stay local.");
    });

    it("reports an unreadable unsynced counter as unknown instead of a partial sum", async () => {
        const text = mountHint(true);
        store.countUnsyncedEvents.mockRejectedValue(new Error("UnsyncedReadFailed"));
        await window.offlineEdit.signalAuthPending(true, "login");
        expect(signals).toHaveLength(1);
        expect(signals[0]).toMatchObject({ pending: true, kind: "login", countUnknown: true });
        expect(console.warn).toHaveBeenCalled();
        // The hint is still shown, but without a made-up number.
        expect(document.querySelector("[data-offline-auth-pending]").hasAttribute("hidden")).toBe(false);
        expect(text.textContent).toBe("Login needed: number of changes unclear.");
    });

    it("reports an unreadable queue counter as unknown, also for the password change text", async () => {
        const text = mountHint(true);
        store.countQueueByStatus.mockRejectedValue(new Error("QueueReadFailed"));
        await window.offlineEdit.signalAuthPending(true, "password-change");
        expect(signals[0]).toMatchObject({ pending: true, kind: "password-change", countUnknown: true });
        expect(text.textContent).toBe("Password change needed: number unclear.");
    });

    it("never shows a digit for an unknown count when the page has no unknown text", async () => {
        const text = mountHint(false);
        store.countUnsyncedEvents.mockRejectedValue(new Error("UnsyncedReadFailed"));
        await window.offlineEdit.signalAuthPending(true, "login");
        expect(text.textContent).toBe("Login needed: ? changes stay local.");
    });
});

describe("offline-queue: auth-pending hint with unreadable counters", () => {
    async function replayIntoAuthRedirect() {
        vi.stubGlobal("fetch", vi.fn(async () => ({
            ok: true,
            redirected: true,
            status: 200,
            url: `${origin}/mfa/setup/`,
            headers: { get: () => null },
            json: async () => ({}),
        })));
        await window.offlineQueue.replayQueue();
        await flush();
    }

    afterEach(() => vi.unstubAllGlobals());

    it("reports the exact number when both sources are readable", async () => {
        await replayIntoAuthRedirect();
        expect(signals).toContainEqual({ pending: true, count: 3, kind: "login" });
    });

    it("reports an unreadable counter as unknown and logs it", async () => {
        store.countUnsyncedEvents.mockRejectedValue(new Error("UnsyncedReadFailed"));
        await replayIntoAuthRedirect();
        const pendingSignals = signals.filter((detail) => detail.pending);
        expect(pendingSignals.length).toBeGreaterThan(0);
        pendingSignals.forEach((detail) => expect(detail.countUnknown).toBe(true));
        expect(console.warn).toHaveBeenCalled();
        expect(unhandled).toEqual([]);
    });
});

describe("offline-client: getLocallyCachedClients", () => {
    it("returns the list when the store is readable", async () => {
        expect(await window.offlineClient.getLocallyCachedClients()).toEqual([{ pk: "c1", lastSynced: "t" }]);
        expect(unknownEvents).toEqual([]);
    });

    it("signals an unreadable list as unknown instead of an empty list", async () => {
        store.listOfflineClients.mockRejectedValue(new Error("ListReadFailed"));
        const result = await window.offlineClient.getLocallyCachedClients();
        expect(result).toBeNull();
        expect(unknownEvents).toEqual([{ source: "clients" }]);
        expect(console.warn).toHaveBeenCalled();
    });
});
