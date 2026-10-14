import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const factories = {};
const messages = {
    offlineMessage: "Connection lost.",
    onlineMessage: "Connection restored.",
    onlineSyncMessage: "Connection restored. Transferring waiting changes.",
    syncCompleteMessage: "Transfer ended.",
    conflictMessage: "New conflict. Open Conflicts using the conflict banner link.",
};
let state;
let history;
let focus;
const queue = (pending, blocked = 0) => state.onQueueEvent({ detail: { pending, blocked, count: pending + blocked } });
const conflicts = (count) => state.onConflictEvent({ detail: { count } });
const flush = () => vi.runAllTimers();
function setup(online = true, dataset = messages) {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(online);
    const root = document.createElement("div");
    Object.assign(root.dataset, dataset);
    root.innerHTML = '<span data-testid="offline-status-announcer"></span>';
    document.body.replaceChildren(root);
    state = factories.offlineStatus();
    state.$el = root;
    // Mirror Alpine's x-text binding and observe each clear/set mutation.
    let value = state.announcement;
    history = [];
    Object.defineProperty(state, "announcement", {
        get: () => value,
        set: (text) => { value = text; root.firstElementChild.textContent = text; history.push(text); },
    });
    state.init?.();
    return state;
}
const spoken = () => history.filter(Boolean);

beforeAll(async () => {
    window.Alpine = { data: (name, factory) => { factories[name] = factory; } };
    await import("../../src/static/js/alpine/base-layout.js");
    document.dispatchEvent(new Event("alpine:init"));
});
beforeEach(() => {
    vi.useFakeTimers();
    focus = vi.spyOn(HTMLElement.prototype, "focus");
    setup();
});
afterEach(() => {
    expect(focus).not.toHaveBeenCalled();
    expect(spoken().every((text) => !/\d/.test(text))).toBe(true);
    state.destroy?.();
    vi.useRealTimers();
    vi.restoreAllMocks();
});

describe("offline status announcements", () => {
    it.each([true, false])("keeps initial connection and first source values silent (online=%s)", (online) => {
        setup(online);
        queue(3, 2);
        conflicts(4);
        flush();
        expect(spoken()).toEqual([]);
        expect(state.announcement).toBe("");
        expect(state.offline).toBe(!online);
    });
    it("announces loss once per connection transition and clears before repeating", () => {
        state.onOffline(); flush();
        expect(spoken()).toEqual([messages.offlineMessage]);
        state.onOffline(); flush();
        expect(spoken()).toHaveLength(1);
        state.onOnline(); flush();
        state.onOnline(); flush();
        state.onOffline();
        expect(state.announcement).toBe("");
        flush();
        expect(spoken()).toEqual([messages.offlineMessage, messages.onlineMessage, messages.offlineMessage]);
        expect(history).toEqual(["", messages.offlineMessage, "", messages.onlineMessage, "", messages.offlineMessage]);
    });
    it.each([0, 3])("announces reconnection with waiting transfer only when pending=%s", (pending) => {
        setup(false); queue(pending);
        state.onOnline(); flush();
        expect(spoken()).toEqual([pending ? messages.onlineSyncMessage : messages.onlineMessage]);
    });
    it("announces only the connected pending-to-empty transition", () => {
        queue(3); queue(2); queue(1); flush();
        expect(spoken()).toEqual([]);
        queue(0); flush(); queue(0); flush();
        expect(spoken()).toEqual([messages.syncCompleteMessage]);
    });
    it("does not announce completion while offline", () => {
        setup(false); queue(1); queue(0); flush();
        expect(spoken()).toEqual([]);
    });
    it("gives a newly blocked entry priority over completion in the same event", () => {
        queue(1); queue(0, 1); flush();
        expect(spoken()).toEqual([messages.conflictMessage]);
    });
    it.each(["queue-first", "conflict-first"])("gives conflict priority across separate events (%s)", (order) => {
        queue(1); conflicts(0);
        if (order === "queue-first") { queue(0); conflicts(1); }
        else { conflicts(1); queue(0); }
        flush();
        expect(spoken()).toEqual([messages.conflictMessage]);
    });
    it("announces increases from each conflict source but never decreasing counts", () => {
        queue(0); conflicts(0);
        conflicts(1); flush();
        queue(0, 1); flush();
        conflicts(0); queue(0, 0); flush();
        expect(spoken()).toEqual([messages.conflictMessage, messages.conflictMessage]);
    });
    it("keeps the first value of the other conflict source silent independently", () => {
        queue(0); queue(0, 1); flush();
        conflicts(5); flush();
        expect(spoken()).toEqual([messages.conflictMessage]);
        conflicts(6); flush();
        expect(spoken()).toHaveLength(2);
    });
    it("keeps nonterminal queue steps, client and unsynced changes silent with existing getters", () => {
        queue(3); queue(2); queue(1);
        state.onClientsEvent({ detail: { count: 1 } });
        state.onUnsyncedEvent({ detail: { count: 2 } });
        conflicts(2); conflicts(1); flush();
        expect(spoken()).toEqual([]);
        expect(state.showSyncBanner).toBe(true);
        expect(state.isSingleQueued).toBe(true);
        expect(state.hasMultipleQueued).toBe(false);
        expect(state.hasCachedClients).toBe(true);
        expect(state.isSingleCachedClient).toBe(true);
        expect(state.hasMultipleCachedClients).toBe(false);
        expect(state.hasUnsynced).toBe(true);
        expect(state.combinedConflictCount).toBe(1);
        expect(state.isSingleConflict).toBe(true);
        expect(state.hasMultipleConflict).toBe(false);
        expect(state.showConflictBanner).toBe(true);
        state.onOffline(); flush();
        expect(state.showSyncBanner).toBe(false);
        expect(state.showConflictBanner).toBe(false);
    });
    it.each(Object.keys(messages))("has no fallback when %s is missing", (key) => {
        const dataset = { ...messages }; delete dataset[key]; setup(true, dataset);
        queue(1); conflicts(0);
        if (key === "offlineMessage") state.onOffline();
        if (key === "onlineMessage") { state.onOffline(); flush(); queue(0); state.onOnline(); }
        if (key === "onlineSyncMessage") { state.onOffline(); flush(); state.onOnline(); }
        if (key === "syncCompleteMessage") queue(0);
        if (key === "conflictMessage") conflicts(1);
        flush();
        expect(state.announcement).toBe("");
    });
    it("cancels delayed announcements when the component is destroyed", () => {
        state.onOffline(); state.destroy?.(); flush();
        expect(spoken()).toEqual([]);
    });
});
