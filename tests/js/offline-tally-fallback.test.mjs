// #1912: Falls der zentrale Orchestrator auf einer Alt-/Teilseite wirklich
// fehlt, bleibt genau ein koaleszierter Online-Fallback erhalten.
import { beforeAll, expect, it, vi } from "vitest";

const revalidateCachedFacility = vi.fn(async () => "not-modified");

beforeAll(async () => {
    delete window.syncOrchestrator;
    globalThis.Alpine = { data: vi.fn() };
    window.crypto_session = { ready: async () => {}, hasSessionKey: () => true };
    window.offlineStore = {
        revalidateCachedFacility,
        getOfflineFacility: async () => null,
        listTallyQueueState: async () => ({ byDocumentType: {}, pending: 0, blocked: 0 }),
    };
    await import("../../src/static/js/offline-tally.js");
    document.dispatchEvent(new Event("DOMContentLoaded"));
});

it("koalesziert konkurrierende Online-Events ohne Orchestrator", async () => {
    window.dispatchEvent(new Event("online"));
    window.dispatchEvent(new Event("online"));

    await vi.waitFor(() => expect(revalidateCachedFacility).toHaveBeenCalledTimes(1));
});
