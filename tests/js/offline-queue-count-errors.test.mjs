// Refs #2626: a failing storage read of the queue counter must be handled,
// reported as an unknown state and recover on the next successful refresh.
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

const unhandled = [];
const onUnhandled = (reason) => unhandled.push(reason);
const events = [];
let countQueueByStatus;

const record = (event) => events.push({ type: event.type, detail: event.detail });
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeAll(async () => {
    process.on("unhandledRejection", onUnhandled);
    window.addEventListener("offline-queue-count", record);
    window.addEventListener("offline-queue-count-unknown", record);
    // The direct init call runs while the module is imported: the first read fails.
    countQueueByStatus = vi.fn(async () => {
        throw new Error("QueueReadFailed");
    });
    window.offlineStore = { countQueueByStatus };
    vi.spyOn(console, "warn").mockImplementation(() => {});
    await import("../../src/static/js/offline-queue.js");
    await flush();
});

afterAll(() => {
    process.off("unhandledRejection", onUnhandled);
    window.removeEventListener("offline-queue-count", record);
    window.removeEventListener("offline-queue-count-unknown", record);
    vi.restoreAllMocks();
});

describe("queue counter read failures", () => {
    it("handles a rejecting read at direct init and reports an unknown state", () => {
        expect(unhandled).toEqual([]);
        expect(events.map((entry) => entry.type)).toEqual(["offline-queue-count-unknown"]);
        // No invented zero counts: no regular count event was emitted.
        expect(events.some((entry) => entry.type === "offline-queue-count")).toBe(false);
    });

    it("keeps refreshCount resolving when the read keeps failing", async () => {
        events.length = 0;
        await expect(window.offlineQueue.refreshCount()).resolves.toBeUndefined();
        await flush();
        expect(unhandled).toEqual([]);
        expect(events.map((entry) => entry.type)).toEqual(["offline-queue-count-unknown"]);
    });

    it("handles a missing store the same way", async () => {
        events.length = 0;
        const store = window.offlineStore;
        delete window.offlineStore;
        try {
            await expect(window.offlineQueue.refreshCount()).resolves.toBeUndefined();
        } finally {
            window.offlineStore = store;
        }
        expect(events.map((entry) => entry.type)).toEqual(["offline-queue-count-unknown"]);
    });

    it("recovers with the real counts after a later successful read", async () => {
        events.length = 0;
        countQueueByStatus.mockResolvedValue({ total: 3, pending: 2, blocked: 1 });
        await window.offlineQueue.refreshCount();
        expect(events).toEqual([
            { type: "offline-queue-count", detail: { count: 3, pending: 2, blocked: 1 } },
        ]);
        expect(unhandled).toEqual([]);
    });
});
