// Refs #2626: unsynced/conflict/client counters report unreadable storage as an
// unknown state instead of swallowing the error and keeping an old number.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const unhandled = [];
const onUnhandled = (reason) => unhandled.push(reason);
const events = [];
const record = (event) => events.push({ type: event.type, detail: event.detail });
const TYPES = ["offline-unsynced-count", "offline-conflict-count", "offline-clients-count", "offline-count-unknown"];
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
let store;

beforeAll(async () => {
    window.crypto_session = { ready: async () => {}, awaitReady: async () => {}, hasSessionKey: () => true };
    window.URL_PATTERNS = { ENDPOINTS: { clientBundle: (pk) => `/clients/${pk}/bundle/` } };
    store = {
        isRevocationPending: async () => false,
        countUnsyncedEvents: vi.fn(async () => 1),
        countConflictEvents: vi.fn(async () => 2),
        countDeadEvents: vi.fn(async () => 1),
        countOfflineClients: vi.fn(async () => 3),
        removeOfflineClient: vi.fn(async () => {}),
    };
    window.offlineStore = store;
    await import("../../src/static/js/offline-form-fields.js");
    await import("../../src/static/js/offline-edit.js");
    await import("../../src/static/js/offline-client.js");
});

beforeEach(() => {
    events.length = 0;
    unhandled.length = 0;
    process.on("unhandledRejection", onUnhandled);
    TYPES.forEach((type) => window.addEventListener(type, record));
    vi.spyOn(console, "warn").mockImplementation(() => {});
    store.countUnsyncedEvents.mockResolvedValue(1);
    store.countConflictEvents.mockResolvedValue(2);
    store.countDeadEvents.mockResolvedValue(1);
    store.countOfflineClients.mockResolvedValue(3);
});

afterEach(() => {
    process.off("unhandledRejection", onUnhandled);
    TYPES.forEach((type) => window.removeEventListener(type, record));
    vi.restoreAllMocks();
});

const sources = () => events.filter((entry) => entry.type === "offline-count-unknown").map((entry) => entry.detail.source);

describe("count sources report read failures", () => {
    it("reports an unreadable unsynced counter as unknown and logs a warning", async () => {
        store.countUnsyncedEvents.mockRejectedValue(new Error("UnsyncedReadFailed"));
        window.offlineEdit.refreshCounts();
        await flush();
        expect(unhandled).toEqual([]);
        expect(sources()).toEqual(["unsynced"]);
        expect(events.some((entry) => entry.type === "offline-unsynced-count")).toBe(false);
        expect(console.warn).toHaveBeenCalled();
        // The conflict counter is independent and still reported.
        expect(events.find((entry) => entry.type === "offline-conflict-count").detail).toEqual({ count: 3 });
    });

    it("reports an unreadable conflict or dead counter as unknown without a made-up count", async () => {
        store.countDeadEvents.mockRejectedValue(new Error("DeadReadFailed"));
        window.offlineEdit.refreshCounts();
        await flush();
        expect(unhandled).toEqual([]);
        expect(sources()).toEqual(["conflict"]);
        expect(events.some((entry) => entry.type === "offline-conflict-count")).toBe(false);
    });

    it("recovers with real counts on the next successful read", async () => {
        store.countUnsyncedEvents.mockRejectedValueOnce(new Error("x"));
        window.offlineEdit.refreshCounts();
        await flush();
        events.length = 0;
        window.offlineEdit.refreshCounts();
        await flush();
        expect(sources()).toEqual([]);
        expect(events.find((entry) => entry.type === "offline-unsynced-count").detail).toEqual({ count: 1 });
    });

    it("reports an unreadable offline client counter as unknown", async () => {
        store.countOfflineClients.mockRejectedValue(new Error("ClientsReadFailed"));
        await window.offlineClient.refreshCountBadge();
        expect(sources()).toEqual(["clients"]);
        expect(events.some((entry) => entry.type === "offline-clients-count")).toBe(false);
        expect(console.warn).toHaveBeenCalled();
        store.countOfflineClients.mockResolvedValue(3);
        events.length = 0;
        await window.offlineClient.refreshCountBadge();
        expect(events.find((entry) => entry.type === "offline-clients-count").detail).toEqual({ count: 3 });
    });
});
