import Dexie from "dexie";
import { expect, it } from "vitest";
import { openParseWindow } from "./_offline_store_modules.mjs";

it("loads the minified classic stack and preserves encrypted storage and private handshakes", async () => {
    window.Dexie = Dexie;
    globalThis.Dexie = Dexie;
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.min.js");
    await window.crypto_session.deriveSessionKey("build test password", "c2FsdHNhbHRzYWx0");
    const endParse = openParseWindow();
    try {
        await import("../../src/static/js/offline-db.min.js");
        await import("../../src/static/js/offline-bundles.min.js");
        await import("../../src/static/js/offline-revocations.min.js");
        await import("../../src/static/js/offline-store.min.js");
        await import("../../src/static/js/offline-queue.min.js");
        await import("../../src/static/js/offline-client.min.js");
        await import("../../src/static/js/offline-edit.min.js");
        await import("../../src/static/js/sync-orchestrator.min.js");
    } finally {
        endParse();
    }
    expect(window.__offlineStoreInternals).toBeUndefined();
    expect(Object.isFrozen(window.crypto_session)).toBe(true);
    expect(window.offlineStore.encryptPayload).toBeUndefined();
    expect(window.offlineStore.decryptPayload).toBeUndefined();
    expect(window.offlineQueue.replayQueue).toBeTypeOf("function");
    expect(window.offlineClient.takeClientOffline).toBeTypeOf("function");
    expect(window.offlineEdit.replayAllModifiedEvents).toBeTypeOf("function");
    expect(window.syncOrchestrator.requestSync).toBeTypeOf("function");
    const id = await window.offlineStore.putEncrypted("queue", {
        url: "/minified-test/", createdAt: Date.now(),
        data: { method: "POST", body: "message=private", headers: {} },
    });
    // The store consumes the crypto handshake lazily on its first encrypted write.
    expect(window.crypto_session._link()).toBeNull();
    try {
        const rows = await window.offlineStore.listDecrypted("queue", row => row.url === "/minified-test/");
        expect(rows).toHaveLength(1);
        expect(rows[0].data.body).toBe("message=private");
        const stored = await window.offlineStore.db.queue.get(id);
        expect(JSON.stringify(stored)).not.toContain("message=private");
    } finally {
        await window.offlineStore.deleteRow("queue", id);
    }
});
