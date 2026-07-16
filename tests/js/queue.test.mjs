// Refs #1479 (TST-01): Enqueue-Guards + Replay-Klassifikation der
// Offline-Queue — genau die Schicht, in der die realen Bugs lagen
// (Stale-CSRF-403 -> Refresh -> Replay; kein Head-of-Line-Blocking bei 4xx;
// Backoff bei 429). Der offlineStore wird gemockt; die Klassifikationslogik
// aus ``replayQueue`` bleibt echt.
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from "vitest";

let offlineQueue;

function makeStore(records) {
    const rows = records.map((r) => ({ ...r }));
    return {
        _rows: rows,
        countQueueByStatus: async () => ({ total: rows.length, pending: rows.length, blocked: 0 }),
        count: async () => rows.length,
        listDecrypted: async () => rows.map((r) => ({ ...r })),
        deleteRow: vi.fn(async (_store, id) => {
            const i = rows.findIndex((r) => r.id === id);
            if (i >= 0) rows.splice(i, 1);
        }),
        putEncrypted: vi.fn(async (_store, row) => {
            const i = rows.findIndex((r) => r.id === row.id);
            if (i >= 0) rows[i] = row;
            else rows.push(row);
        }),
        coalescePendingQueueByUrl: vi.fn(async () => false),
    };
}

function resp(overrides) {
    return {
        ok: false,
        redirected: false,
        status: 200,
        url: "https://example.test/x",
        headers: { get: () => null },
        json: async () => ({}),
        ...overrides,
    };
}

function record(overrides) {
    return {
        id: 1,
        url: "https://example.test/api/v1/events/create/",
        attempts: 0,
        retryAfter: 0,
        idempotencyKey: "idem-1",
        data: { method: "POST", body: "{}", headers: { "hx-request": "true" } },
        ...overrides,
    };
}

beforeAll(async () => {
    window.offlineStore = makeStore([]);
    window.crypto_session = { ready: async () => {}, hasSessionKey: () => true };
    window.csrfUtils = { fromMeta: () => "META-TOKEN", refresh: async () => "FRESH-TOKEN" };
    await import("../../src/static/js/offline-queue.js");
    offlineQueue = window.offlineQueue;
});

beforeEach(() => {
    window.crypto_session = { ready: async () => {}, hasSessionKey: () => true };
    window.csrfUtils = { fromMeta: () => "META-TOKEN", refresh: async () => "FRESH-TOKEN" };
});

afterEach(() => {
    vi.restoreAllMocks();
    delete global.fetch;
});

describe("enqueueRequest (guards)", () => {
    it("weist ohne Session-Key ab (kein Klartext-Leak)", async () => {
        window.crypto_session = { ready: async () => {}, hasSessionKey: () => false };
        window.offlineStore = makeStore([]);
        await expect(
            offlineQueue.enqueueRequest("/x", "POST", "{}", { "content-type": "application/json" })
        ).rejects.toMatchObject({ name: "NoSessionKeyError" });
    });

    it("weist multipart/form-data ab", async () => {
        window.offlineStore = makeStore([]);
        await expect(
            offlineQueue.enqueueRequest("/x", "POST", "body", {
                "content-type": "multipart/form-data; boundary=abc",
            })
        ).rejects.toMatchObject({ name: "OfflineUploadError" });
    });

    it("schreibt einen Queue-Eintrag mit Idempotenz-Schluessel", async () => {
        const store = makeStore([]);
        window.offlineStore = store;
        await offlineQueue.enqueueRequest("/api/v1/events/create/", "POST", "{}", {
            "content-type": "application/json",
        });
        expect(store.putEncrypted).toHaveBeenCalledTimes(1);
        const [storeName, row] = store.putEncrypted.mock.calls[0];
        expect(storeName).toBe("queue");
        expect(row.url).toBe("/api/v1/events/create/");
        expect(row.idempotencyKey).toBeTruthy();
        expect(row.attempts).toBe(0);
    });
});

describe("replayQueue (classification)", () => {
    it("200 (HTMX) -> Erfolg, Eintrag geloescht", async () => {
        const store = makeStore([record()]);
        window.offlineStore = store;
        global.fetch = vi.fn(async () => resp({ ok: true, status: 200 }));
        await offlineQueue.replayQueue();
        expect(store.deleteRow).toHaveBeenCalledWith("queue", 1);
        expect(store._rows).toHaveLength(0);
    });

    it("409 -> Konflikt-Status, kein Loeschen, Schleife laeuft weiter", async () => {
        const store = makeStore([record()]);
        window.offlineStore = store;
        global.fetch = vi.fn(async () =>
            resp({ status: 409, json: async () => ({ error: "conflict", server_state: { titel: "S" } }) })
        );
        await offlineQueue.replayQueue();
        expect(store.deleteRow).not.toHaveBeenCalled();
        const saved = store.putEncrypted.mock.calls.find((c) => c[1].localStatus === "conflict");
        expect(saved).toBeTruthy();
        expect(saved[1].data.conflict.serverState).toEqual({ titel: "S" });
    });

    it("422 -> dead-letter (kein Head-of-Line-Blocking)", async () => {
        const store = makeStore([record()]);
        window.offlineStore = store;
        global.fetch = vi.fn(async () => resp({ status: 422 }));
        await offlineQueue.replayQueue();
        const saved = store.putEncrypted.mock.calls.find((c) => c[1].localStatus === "dead");
        expect(saved).toBeTruthy();
        expect(saved[1].deadReason).toBe("invalid");
    });

    it("429 -> Backoff gesetzt, Schleife bricht ab", async () => {
        const store = makeStore([record()]);
        window.offlineStore = store;
        global.fetch = vi.fn(async () => resp({ status: 429 }));
        const before = Date.now();
        await offlineQueue.replayQueue();
        const saved = store.putEncrypted.mock.calls.find((c) => c[1].lastError === "429");
        expect(saved).toBeTruthy();
        expect(saved[1].retryAfter).toBeGreaterThan(before);
    });

    it("403 -> CSRF-Refresh-Retry, dann Erfolg (Regression #1330/#1332)", async () => {
        const store = makeStore([record()]);
        window.offlineStore = store;
        window.csrfUtils = { fromMeta: () => "STALE", refresh: vi.fn(async () => "FRESH") };
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(resp({ status: 403 }))
            .mockResolvedValueOnce(resp({ ok: true, status: 200 }));
        global.fetch = fetchMock;
        await offlineQueue.replayQueue();
        expect(window.csrfUtils.refresh).toHaveBeenCalled();
        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(store.deleteRow).toHaveBeenCalledWith("queue", 1);
    });

    it("403 bleibt 403 trotz Refresh -> dead (forbidden)", async () => {
        const store = makeStore([record()]);
        window.offlineStore = store;
        window.csrfUtils = { fromMeta: () => "STALE", refresh: async () => "FRESH" };
        // Beide Versuche 403 -> kein Token-, sondern Rechteproblem.
        global.fetch = vi.fn(async () => resp({ status: 403 }));
        await offlineQueue.replayQueue();
        const saved = store.putEncrypted.mock.calls.find((c) => c[1].localStatus === "dead");
        expect(saved).toBeTruthy();
        expect(saved[1].deadReason).toBe("forbidden");
    });
});

describe("offlineQueue._refreshCsrf (delegation)", () => {
    it("delegiert an csrfUtils.refresh", async () => {
        window.csrfUtils = { fromMeta: () => "M", refresh: async () => "DELEGATED" };
        await expect(offlineQueue._refreshCsrf()).resolves.toBe("DELEGATED");
    });
});
