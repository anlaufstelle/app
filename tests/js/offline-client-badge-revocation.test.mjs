// Refs #2730: the saved-bundle notice must not wait for the exclusive revocation check.
import { afterEach, expect, it, vi } from "vitest";

afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
});

it("reports a hanging registration after saving the bundle while revocation is still pending", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const phases = {};
    let factory;
    vi.stubGlobal("Alpine", { data: (_name, value) => { factory = value; } });
    vi.stubGlobal("crypto_session", { hasSessionKey: () => true, isSupported: () => true });
    vi.stubGlobal("URL_PATTERNS", { ENDPOINTS: { clientBundle: () => "/clients/one/bundle/" } });
    vi.stubGlobal("offlineStore", {
        MAX_OFFLINE_CLIENTS: 50,
        ensurePersistentStorage: async () => true,
        countOfflineClients: async () => 1,
        isClientOffline: async () => true,
        getOfflineClient: async () => ({}),
        saveClientBundle: async () => { phases.savedAt = Date.now(); return {}; },
    });
    vi.stubGlobal("fetch", vi.fn(() => new Promise((resolve) => {
        phases.fetchStartedAt = Date.now();
        setTimeout(() => {
            phases.fetchFinishedAt = Date.now();
            resolve({ ok: true, headers: { get: () => null }, json: async () => ({}) });
        }, 250);
    })));
    let releaseRevocation;
    vi.stubGlobal("syncOrchestrator", {
        requestRevocationCheck: vi.fn(() => {
            phases.revocationStartedAt = Date.now();
            return new Promise((resolve) => { releaseRevocation = () => {
                phases.revocationFinishedAt = Date.now();
                resolve();
            }; });
        }),
    });
    vi.stubGlobal("offlineWorker", {
        retry: vi.fn(() => {
            phases.registrationStartedAt = Date.now();
            return new Promise(() => {});
        }),
        isUnavailable: () => false,
    });
    await import("../../src/static/js/offline-client.js");
    await import("../../src/static/js/offline-client-badge.js");
    document.dispatchEvent(new Event("alpine:init"));
    const badge = factory();
    badge._pk = "one";
    const show = badge.showMessage.bind(badge);
    badge.showMessage = (text, type) => { phases.messageAt = Date.now(); show(text, type); };

    const click = badge.toggleOffline();
    await vi.advanceTimersByTimeAsync(4250);
    const noticeBeforeRevocation = badge.message;
    const busyBeforeRevocation = badge.busy;
    // Measure the old serial path as well, so a red run records its full delay.
    await vi.advanceTimersByTimeAsync(16000);
    releaseRevocation();
    await vi.advanceTimersByTimeAsync(4000);
    await click;
    console.info("take-offline timing (fake milliseconds)", JSON.stringify(phases));

    expect(phases.fetchFinishedAt - phases.fetchStartedAt).toBe(250);
    expect(phases.revocationFinishedAt - phases.revocationStartedAt).toBe(20000);
    expect(noticeBeforeRevocation).toContain("nicht verfügbar");
    expect(busyBeforeRevocation).toBe(false);
    expect(phases.messageAt).toBe(4250);
    expect(window.syncOrchestrator.requestRevocationCheck).toHaveBeenCalledTimes(1);
});
