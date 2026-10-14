import { afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";

beforeAll(async () => {
    const state = vi.spyOn(document, "readyState", "get").mockReturnValue("loading");
    vi.stubGlobal("BroadcastChannel", undefined);
    await import("../../src/static/js/sync-orchestrator.js");
    await import("../../src/static/js/offline-client.js");
    state.mockRestore();
});

beforeEach(() => {
    window.crypto_session = { awaitReady: vi.fn(async () => {}), hasSessionKey: () => true };
    window.URL_PATTERNS = { ENDPOINTS: { clientBundle: (pk) => `/clients/${pk}/bundle/` } };
    window.offlineQueue = { replayQueue: vi.fn() };
    window.offlineEdit = { replayAllModifiedEvents: vi.fn() };
    window.offlineStore = {
        MAX_OFFLINE_CLIENTS: 50,
        ensurePersistentStorage: vi.fn(async () => true),
        countOfflineClients: vi.fn(async () => 1),
        count: vi.fn(async () => 0),
        isClientOffline: vi.fn(async () => false),
        saveClientBundle: vi.fn(async () => ({})),
        getMetaValue: vi.fn(async () => ({ checkedAt: Date.now() })),
        setMetaValue: vi.fn(async () => {}),
        applyClientRevocations: vi.fn(async () => ({ status: "ok" })),
    };
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, headers: { get: () => null }, json: async () => ({ client: { pk: "client-1" } }) })));
});
afterEach(() => { vi.restoreAllMocks(); });

it("checks revocations immediately after storing a take even after a recent boot check", async () => {
    await window.offlineClient.takeClientOffline("client-1");
    expect(window.offlineStore.applyClientRevocations).toHaveBeenCalledTimes(1);
    expect(window.offlineQueue.replayQueue).not.toHaveBeenCalled();
    expect(window.offlineEdit.replayAllModifiedEvents).not.toHaveBeenCalled();
    expect(window.offlineStore.saveClientBundle.mock.invocationCallOrder[0]).toBeLessThan(
        window.offlineStore.applyClientRevocations.mock.invocationCallOrder[0],
    );
});

it("coalesces rapid repeated takes while the revocation request is pending", async () => {
    let release;
    window.offlineStore.applyClientRevocations.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    const first = window.offlineClient.takeClientOffline("client-1");
    const second = window.offlineClient.takeClientOffline("client-1");
    await vi.waitFor(() => expect(window.offlineStore.applyClientRevocations).toHaveBeenCalledTimes(1));
    release({ status: "ok" });
    await Promise.all([first, second]);
    expect(window.offlineStore.applyClientRevocations).toHaveBeenCalledTimes(1);
});

it("waits for key preparation before fetching a bundle", async () => {
    let release;
    window.crypto_session.awaitReady.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    const take = window.offlineClient.takeClientOffline("client-1");
    expect(fetch).not.toHaveBeenCalled();
    expect(window.offlineStore.saveClientBundle).not.toHaveBeenCalled();
    release();
    await take;
    expect(fetch).toHaveBeenCalledTimes(1);
});

it("retains the pending notice on a failed check without starting replay", async () => {
    document.body.innerHTML = '<div data-offline-revocation-pending hidden></div>';
    window.offlineStore.applyClientRevocations.mockResolvedValue({ status: "error" });
    await window.offlineClient.takeClientOffline("client-1");
    expect(window.offlineStore.applyClientRevocations).toHaveBeenCalledTimes(1);
    expect(document.querySelector("[data-offline-revocation-pending]").hidden).toBe(false);
    expect(window.offlineQueue.replayQueue).not.toHaveBeenCalled();
    expect(window.offlineEdit.replayAllModifiedEvents).not.toHaveBeenCalled();
});
