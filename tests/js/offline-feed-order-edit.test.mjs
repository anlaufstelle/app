// Refs #2798: The offline feed order needs the recording time of every entry.
// A new offline entry stamps it once when it is saved; an edit of a synced
// contact copies the server's created_at into the envelope that replaces the
// clean record, and a re-edit keeps both unchanged.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const CREATED = "2026-11-14T12:03:20+00:00";
let rows;

function makeStore() {
    rows = new Map();
    return {
        saveOfflineEdit: vi.fn(async (record) => {
            rows.set(record.pk, JSON.parse(JSON.stringify(record)));
        }),
        getOfflineEvent: vi.fn(async (pk) => {
            const row = rows.get(String(pk));
            return row ? JSON.parse(JSON.stringify(row)) : null;
        }),
        ensurePersistentStorage: vi.fn(async () => true),
        countUnsyncedEvents: vi.fn(async () => 1),
        countConflictEvents: vi.fn(async () => 0),
        countDeadEvents: vi.fn(async () => 0),
    };
}

beforeAll(async () => {
    window.crypto_session = { awaitReady: vi.fn(async () => {}), hasSessionKey: vi.fn(() => true) };
    window.csrfUtils = { fromMeta: vi.fn(() => "csrf-token"), refresh: vi.fn(async () => "csrf-token") };
    window.offlineStore = makeStore();
    await import("../../src/static/js/offline-edit.js");
});

beforeEach(() => {
    vi.useRealTimers();
    window.offlineStore = makeStore();
});

describe("recording time of offline entries", () => {
    it("stamps a new entry once and keeps it through a re-edit", async () => {
        vi.useFakeTimers({ toFake: ["Date"] });
        vi.setSystemTime(new Date("2026-11-14T12:03:40Z"));
        const created = await window.offlineEdit.markEventNew("client-1", "type-1", { a: 1 }, { occurredAt: "2026-11-14T12:03" });
        expect(created.data.createdAt).toBe("2026-11-14T12:03:40.000Z");

        vi.setSystemTime(new Date("2026-11-14T12:09:00Z"));
        await window.offlineEdit.markEventModified(created.pk, { a: 2 }, {});
        const row = await window.offlineStore.getOfflineEvent(created.pk);
        expect(row.localStatus).toBe("new");
        expect(row.data.createdAt).toBe("2026-11-14T12:03:40.000Z");
    });

    it("copies created_at from the synced contact into its edit envelope", async () => {
        rows.set("event-1", {
            pk: "event-1",
            clientPk: "client-1",
            localStatus: "clean",
            data: { pk: "event-1", occurred_at: "2026-11-14T12:03:00+00:00", created_at: CREATED, updated_at: "u1" },
        });
        await window.offlineEdit.markEventModified("event-1", { a: 2 }, { occurredAt: "2026-11-14T12:03:00+00:00" });
        const row = await window.offlineStore.getOfflineEvent("event-1");
        expect(row.localStatus).toBe("modified");
        expect(row.data.createdAt).toBe(CREATED);
    });

    it("keeps the copied created_at through a second edit", async () => {
        rows.set("event-1", {
            pk: "event-1",
            clientPk: "client-1",
            localStatus: "modified",
            data: { formData: {}, occurredAt: "2026-11-14T12:03:00+00:00", createdAt: CREATED, expectedUpdatedAt: "u1" },
        });
        await window.offlineEdit.markEventModified("event-1", { a: 3 }, { occurredAt: "2026-11-14T12:03:00+00:00" });
        const row = await window.offlineStore.getOfflineEvent("event-1");
        expect(row.data.createdAt).toBe(CREATED);
    });
});
