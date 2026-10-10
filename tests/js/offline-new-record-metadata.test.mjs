// Refs #2293: Eine offline angelegte, noch nie synchronisierte Neuanlage darf
// beim erneuten Bearbeiten ihre Create-Metadaten nicht verlieren. Der
// Create-Replay baut ``client``/``document_type``/``case``/``occurred_at``
// ausschliesslich aus dem gespeicherten Envelope — faellt dort z.B. ``casePk``
// weg, landet das Ereignis nach der Synchronisation ausserhalb des Falls.
//
// Die Tests greifen bewusst den REQUEST-BODY ab (nicht das Ergebnis-Status-
// Feld): die Erfolgsklassifikation aendert sich mit #2289, die Frage "welcher
// Fall geht an den Server?" bleibt davon unberuehrt.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const clientPk = "22222222-2222-4222-8222-222222222222";
const casePk = "33333333-3333-4333-8333-333333333333";
const docTypePk = "44444444-4444-4444-8444-444444444444";

let store;
let rows;

function makeStore() {
    rows = new Map();
    return {
        // Replay classification assumes a previously validated revocation list.
        isRevocationPending: async () => false,
        saveOfflineEdit: vi.fn(async (record) => {
            rows.set(record.pk, JSON.parse(JSON.stringify(record)));
        }),
        getOfflineEvent: vi.fn(async (pk) => {
            const row = rows.get(String(pk));
            return row ? JSON.parse(JSON.stringify(row)) : null;
        }),
        ensurePersistentStorage: vi.fn(async () => true),
        updateEventLocalStatus: vi.fn(async () => {}),
        markEventDead: vi.fn(async () => {}),
        saveConflictState: vi.fn(async () => {}),
        clearOfflineEdit: vi.fn(async () => {}),
        countUnsyncedEvents: vi.fn(async () => 1),
        countConflictEvents: vi.fn(async () => 0),
        countDeadEvents: vi.fn(async () => 0),
    };
}

/** Den zuletzt an fetch uebergebenen Formularkoerper als URLSearchParams. */
function lastBody(fetchMock) {
    const call = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
    return new URLSearchParams(call[1].body);
}

function okResponse() {
    return {
        ok: true,
        redirected: true,
        status: 200,
        url: "https://example.test/events/abc/",
        headers: { get: (name) => (name === "X-Offline-Replay-Result" ? "saved" : null) },
        json: async () => ({ status: "saved" }),
    };
}

beforeAll(async () => {
    Object.defineProperty(window.navigator, "onLine", { configurable: true, value: true });
    window.crypto_session = {
        awaitReady: vi.fn(async () => {}),
        hasSessionKey: vi.fn(() => true),
    };
    window.csrfUtils = {
        fromMeta: vi.fn(() => "csrf-token"),
        refresh: vi.fn(async () => "fresh-csrf-token"),
    };
    store = makeStore();
    window.offlineStore = store;
    await import("../../src/static/js/offline-edit.js");
});

beforeEach(() => {
    vi.clearAllMocks();
    rows.clear();
});

describe("#2293 — Create-Metadaten ueberleben das erneute Bearbeiten", () => {
    it("schickt den gewaehlten Fall auch nach einer Feldkorrektur mit", async () => {
        const created = await window.offlineEdit.markEventNew(
            clientPk,
            docTypePk,
            { notiz: "erste Fassung" },
            { casePk: casePk, occurredAt: "2026-09-22T10:00", documentTypeName: "Beratung" }
        );

        const reedited = await window.offlineEdit.markEventModified(
            created.pk,
            { notiz: "korrigierte Fassung" },
            {
                clientPk: clientPk,
                occurredAt: "2026-09-22T10:00",
                documentTypePk: docTypePk,
                documentTypeName: "Beratung",
                expectedUpdatedAt: "",
            }
        );

        expect(reedited.localStatus).toBe("new");
        expect(reedited.data.casePk).toBe(casePk);
        expect(reedited.data.idempotencyKey).toBe(created.data.idempotencyKey);

        const fetchMock = vi.fn(async () => okResponse());
        vi.stubGlobal("fetch", fetchMock);
        await window.offlineEdit.replayModifiedEvent(await store.getOfflineEvent(created.pk), {
            replay: true,
        });

        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(fetchMock.mock.calls[0][0]).toBe("/events/new/");
        const body = lastBody(fetchMock);
        expect(body.get("case")).toBe(casePk);
        expect(body.get("client")).toBe(clientPk);
        expect(body.get("document_type")).toBe(docTypePk);
        expect(body.get("occurred_at")).toBe("2026-09-22T10:00");
        expect(body.get("notiz")).toBe("korrigierte Fassung");
    });

    it("erhaelt Fall, Neuanlagenstatus und Idempotenzschluessel ueber mehrere Bearbeitungen", async () => {
        const created = await window.offlineEdit.markEventNew(
            clientPk,
            docTypePk,
            { notiz: "v1" },
            { casePk: casePk, occurredAt: "2026-09-22T10:00" }
        );

        await window.offlineEdit.markEventModified(created.pk, { notiz: "v2" }, { clientPk: clientPk });
        const third = await window.offlineEdit.markEventModified(
            created.pk,
            { notiz: "v3" },
            { clientPk: clientPk }
        );

        expect(third.localStatus).toBe("new");
        expect(third.data.casePk).toBe(casePk);
        expect(third.data.documentTypePk).toBe(docTypePk);
        expect(third.data.idempotencyKey).toBe(created.data.idempotencyKey);

        const fetchMock = vi.fn(async () => okResponse());
        vi.stubGlobal("fetch", fetchMock);
        await window.offlineEdit.replayModifiedEvent(await store.getOfflineEvent(created.pk), {
            replay: true,
        });

        const body = lastBody(fetchMock);
        expect(body.get("case")).toBe(casePk);
        expect(body.get("notiz")).toBe("v3");
        const headers = fetchMock.mock.calls[0][1].headers;
        expect(headers["X-Idempotency-Key"]).toBe(created.data.idempotencyKey);
    });

    it("laesst eine Neuanlage ohne Fall ohne Fall", async () => {
        const created = await window.offlineEdit.markEventNew(
            clientPk,
            docTypePk,
            { notiz: "ohne Fall" },
            { occurredAt: "2026-09-22T10:00" }
        );

        await window.offlineEdit.markEventModified(created.pk, { notiz: "ohne Fall, korrigiert" }, {
            clientPk: clientPk,
        });

        const fetchMock = vi.fn(async () => okResponse());
        vi.stubGlobal("fetch", fetchMock);
        await window.offlineEdit.replayModifiedEvent(await store.getOfflineEvent(created.pk), {
            replay: true,
        });

        const body = lastBody(fetchMock);
        expect(body.has("case")).toBe(false);
        expect(body.get("notiz")).toBe("ohne Fall, korrigiert");
    });

    it("degradiert einen bearbeiteten Bestands-Datensatz nicht zur Neuanlage", async () => {
        // Gegenprobe: ein aus dem Bundle stammender CLEAN-Record traegt keine
        // Create-Metadaten — der Re-Edit bleibt "modified" und geht an /edit/.
        const eventPk = "55555555-5555-4555-8555-555555555555";
        rows.set(eventPk, {
            pk: eventPk,
            clientPk: clientPk,
            occurredAt: "2026-09-20T09:00",
            localStatus: "clean",
            data: { updated_at: "2026-09-20T09:30:00Z" },
        });

        const record = await window.offlineEdit.markEventModified(
            eventPk,
            { notiz: "bearbeitet" },
            { clientPk: clientPk }
        );

        expect(record.localStatus).toBe("modified");
        expect(record.data.casePk).toBeUndefined();
        expect(record.data.idempotencyKey).toBeUndefined();
        expect(record.data.expectedUpdatedAt).toBe("2026-09-20T09:30:00Z");

        const fetchMock = vi.fn(async () => okResponse());
        vi.stubGlobal("fetch", fetchMock);
        await window.offlineEdit.replayModifiedEvent(await store.getOfflineEvent(eventPk), { replay: true });

        expect(fetchMock.mock.calls[0][0]).toBe("/events/" + eventPk + "/edit/");
    });
});
