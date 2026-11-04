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
});
