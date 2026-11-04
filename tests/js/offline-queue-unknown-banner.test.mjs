// Refs #2626: the global status component marks an unreadable queue counter as
// unclear, keeps the last known counts and clears the state on recovery.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const factories = {};
let state;
const flush = () => vi.runAllTimers();

beforeAll(async () => {
    window.Alpine = { data: (name, factory) => { factories[name] = factory; } };
    await import("../../src/static/js/alpine/base-layout.js");
    document.dispatchEvent(new Event("alpine:init"));
});

beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
    const root = document.createElement("div");
    root.dataset.queueUnknownMessage = "Queue state unclear.";
    root.innerHTML = '<span data-testid="offline-status-announcer"></span>';
    document.body.replaceChildren(root);
    state = factories.offlineStatus();
    state.$el = root;
    Object.defineProperty(state, "announcement", {
        get: () => root.firstElementChild.textContent,
        set: (text) => { root.firstElementChild.textContent = text; },
    });
});

afterEach(() => {
    state.destroy?.();
    vi.useRealTimers();
    vi.restoreAllMocks();
    delete window.offlineQueue;
});

describe("unknown queue state in the status component", () => {
    it("flags the state, keeps the last known counts and announces once", () => {
        state.onQueueEvent({ detail: { pending: 3, blocked: 2, count: 5 } });
        state.onQueueUnknownEvent({ detail: {} });
        state.onQueueUnknownEvent({ detail: {} });
        flush();
        expect(state.queueUnknown).toBe(true);
        expect(state.queueCount).toBe(3);
        expect(state.queueBlocked).toBe(2);
        expect(state.$el.firstElementChild.textContent).toBe("Queue state unclear.");
    });

    it("clears the flag once a real count arrives", () => {
        state.onQueueUnknownEvent({ detail: {} });
        expect(state.queueUnknown).toBe(true);
        state.onQueueEvent({ detail: { pending: 0, blocked: 0, count: 0 } });
        expect(state.queueUnknown).toBe(false);
    });

    it("retries through the queue refresh", () => {
        window.offlineQueue = { refreshCount: vi.fn(async () => {}) };
        state.retryQueueCount();
        expect(window.offlineQueue.refreshCount).toHaveBeenCalledOnce();
    });

    it("tolerates a missing queue module on retry", () => {
        delete window.offlineQueue;
        expect(() => state.retryQueueCount()).not.toThrow();
    });

    it("hides the counts of an unreadable source instead of offering them", () => {
        state.onQueueEvent({ detail: { pending: 3, blocked: 2, count: 5 } });
        state.onConflictEvent({ detail: { count: 4 } });
        state.onUnsyncedEvent({ detail: { count: 2 } });
        state.onClientsEvent({ detail: { count: 5 } });
        state.offline = false;
        expect(state.showConflictBanner).toBe(true);
        expect(state.showSyncBanner).toBe(true);

        state.onCountUnknownEvent({ detail: { source: "conflict" } });
        expect(state.queueUnknown).toBe(true);
        expect(state.showConflictBanner).toBe(false);
        expect(state.showSyncBanner).toBe(true);
        // The numbers themselves are not discarded.
        expect(state.conflictCount).toBe(4);

        state.onCountUnknownEvent({ detail: { source: "unsynced" } });
        expect(state.hasUnsynced).toBe(false);
        state.onCountUnknownEvent({ detail: { source: "clients" } });
        expect(state.hasCachedClients).toBe(false);
        // A client count alone does not raise the "changes unclear" banner.
        state.onConflictEvent({ detail: { count: 4 } });
        state.onUnsyncedEvent({ detail: { count: 2 } });
        expect(state.queueUnknown).toBe(false);
        expect(state.showConflictBanner).toBe(true);
        state.onClientsEvent({ detail: { count: 5 } });
        expect(state.hasCachedClients).toBe(true);
    });

    it("hides the conflict banner while the queue counter is unreadable and recovers", () => {
        state.offline = false;
        state.onQueueEvent({ detail: { pending: 0, blocked: 2, count: 2 } });
        expect(state.showConflictBanner).toBe(true);
        state.onQueueUnknownEvent();
        expect(state.showConflictBanner).toBe(false);
        expect(state.showSyncBanner).toBe(false);
        state.onQueueEvent({ detail: { pending: 0, blocked: 2, count: 2 } });
        expect(state.showConflictBanner).toBe(true);
    });

    it("announces once for several unreadable sources and retries all counters", () => {
        state.onCountUnknownEvent({ detail: { source: "conflict" } });
        state.onCountUnknownEvent({ detail: { source: "unsynced" } });
        state.onQueueUnknownEvent();
        flush();
        expect(state.$el.firstElementChild.textContent).toBe("Queue state unclear.");
        window.offlineQueue = { refreshCount: vi.fn(async () => {}) };
        window.offlineEdit = { refreshCounts: vi.fn() };
        window.offlineClient = { refreshCountBadge: vi.fn(async () => {}) };
        state.retryQueueCount();
        expect(window.offlineQueue.refreshCount).toHaveBeenCalledOnce();
        expect(window.offlineEdit.refreshCounts).toHaveBeenCalledOnce();
        expect(window.offlineClient.refreshCountBadge).toHaveBeenCalledOnce();
        delete window.offlineEdit;
        delete window.offlineClient;
    });
});
