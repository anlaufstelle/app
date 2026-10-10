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
        // Replay classification assumes a previously validated revocation list.
        isRevocationPending: async () => false,
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
        url: `${window.location.origin}/events/new/`,
        attempts: 0,
        retryAfter: 0,
        idempotencyKey: "idem-1",
        data: { method: "POST", body: "{}", headers: { "hx-request": "true" } },
        ...overrides,
    };
}

beforeAll(async () => {
    await import("../../src/static/js/url-patterns.js");
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
        await offlineQueue.enqueueRequest("/events/new/", "POST", "{}", {
            "content-type": "application/json",
        });
        expect(store.putEncrypted).toHaveBeenCalledTimes(1);
        const [storeName, row] = store.putEncrypted.mock.calls[0];
        expect(storeName).toBe("queue");
        expect(row.url).toBe("/events/new/");
        expect(row.idempotencyKey).toBeTruthy();
        expect(row.attempts).toBe(0);
    });

    it.each([
        "https://evil.example/events/new/",
        `${window.location.origin}/partials/tally/increment/extra/`,
    ])("weist unsichere Queue-URL %s vor dem Speichern ab", async (url) => {
        const store = makeStore([]);
        window.offlineStore = store;

        await expect(
            offlineQueue.enqueueRequest(url, "POST", "secret=never-store", {
                "content-type": "application/x-www-form-urlencoded",
            })
        ).rejects.toMatchObject({ name: "UnsafeQueueUrlError" });
        expect(store.putEncrypted).not.toHaveBeenCalled();
    });

    it.each(["GET", "DELETE", "PATCH"])(
        "weist unsichere Queue-Methode %s vor dem Speichern ab",
        async (method) => {
            const store = makeStore([]);
            window.offlineStore = store;

            await expect(
                offlineQueue.enqueueRequest("/events/new/", method, "secret=never-store", {
                    "content-type": "application/x-www-form-urlencoded",
                })
            ).rejects.toMatchObject({ name: "UnsafeQueueMethodError" });
            expect(store.putEncrypted).not.toHaveBeenCalled();
        }
    );

    it.each([
        "/events/new/",
        `/workitems/${"11111111-1111-4111-8111-111111111111"}/edit/`,
        `/partials/workitems/${"11111111-1111-4111-8111-111111111111"}/status/`,
        "/partials/tally/increment/",
        "/api/v1/offline/audit/note-export/",
    ])("erlaubt bestehenden Write-Pfad %s", async (url) => {
        const store = makeStore([]);
        window.offlineStore = store;
        await offlineQueue.enqueueRequest(url, "POST", "x=1", {
            "content-type": "application/x-www-form-urlencoded",
        });
        expect(store.putEncrypted).toHaveBeenCalledTimes(1);
    });

    it.each(["POST", "PUT"])("erlaubt Write-Methode %s", async (method) => {
        const store = makeStore([]);
        window.offlineStore = store;

        await offlineQueue.enqueueRequest("/events/new/", method, "x=1", {
            "content-type": "application/x-www-form-urlencoded",
        });

        expect(store.putEncrypted).toHaveBeenCalledTimes(1);
        expect(store.putEncrypted.mock.calls[0][1].data.method).toBe(method);
    });
});

describe("replayQueue (classification)", () => {
    it.each([
        "https://evil.example/events/new/",
        `${window.location.origin}/partials/tally/increment-evil/`,
    ])("dead-lettert manipulierte gespeicherte URL %s ohne Fetch", async (url) => {
        const store = makeStore([record({ url, data: { method: "POST", body: "secret=value", headers: {} } })]);
        window.offlineStore = store;
        global.fetch = vi.fn();

        await offlineQueue.replayQueue();

        expect(global.fetch).not.toHaveBeenCalled();
        const saved = store.putEncrypted.mock.calls.find((call) => call[1].localStatus === "dead");
        expect(saved[1].deadReason).toBe("unsafe-url");
        expect(saved[1].lastError).not.toContain("secret");
        expect(saved[1].lastError).not.toContain("evil.example");
    });

    it("dead-lettert manipulierte DELETE-Methode ohne Fetch", async () => {
        const store = makeStore([
            record({ data: { method: "DELETE", body: "secret=value", headers: {} } }),
        ]);
        window.offlineStore = store;
        global.fetch = vi.fn();

        await offlineQueue.replayQueue();

        expect(global.fetch).not.toHaveBeenCalled();
        const saved = store.putEncrypted.mock.calls.find((call) => call[1].localStatus === "dead");
        expect(saved[1].deadReason).toBe("unsafe-method");
        expect(saved[1].lastError).toBe("blocked");
        expect(saved[1].lastError).not.toContain("DELETE");
        expect(saved[1].lastError).not.toContain("secret");
    });

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
        expect(store.deleteRow).not.toHaveBeenCalled();
        expect(store._rows).toHaveLength(1);
        const saved = store.putEncrypted.mock.calls.find((c) => c[1].localStatus === "dead");
        expect(saved).toBeTruthy();
        expect(saved[1].deadReason).toBe("invalid");
    });

    it.each([404, 410])("%s -> sichtbares dead-letter fuer geloeschtes/deaktiviertes Ziel", async (status) => {
        const store = makeStore([record()]);
        window.offlineStore = store;
        global.fetch = vi.fn(async () => resp({ status }));

        await offlineQueue.replayQueue();

        const saved = store.putEncrypted.mock.calls.find((call) => call[1].localStatus === "dead");
        expect(saved).toBeTruthy();
        expect(saved[1].deadReason).toBe("not-found");
        expect(saved[1].lastError).toBe(String(status));
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
