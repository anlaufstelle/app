// Refs #2187: 422 auf dem pk-losen Create-Replay muss den Record dead-lettert,
// damit die Offline-Doppelstunde sichtbar wird statt endlos erneut abgelehnt.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const eventPk = "11111111-1111-4111-8111-111111111111";
let offlineStore;

beforeAll(async () => {
    Object.defineProperty(window.navigator, "onLine", { configurable: true, value: true });
    window.crypto_session = {
        ready: vi.fn(async () => {}),
        hasSessionKey: vi.fn(() => true),
    };
    window.csrfUtils = {
        fromMeta: vi.fn(() => "csrf-token"),
        refresh: vi.fn(async () => "fresh-csrf-token"),
    };
    offlineStore = {
        updateEventLocalStatus: vi.fn(async () => {}),
        markEventDead: vi.fn(async () => {}),
        saveConflictState: vi.fn(async () => {}),
        countUnsyncedEvents: vi.fn(async () => 1),
        countConflictEvents: vi.fn(async () => 0),
        countDeadEvents: vi.fn(async () => 0),
    };
    window.offlineStore = offlineStore;
    await import("../../src/static/js/offline-edit.js");
});

beforeEach(() => {
    vi.clearAllMocks();
});

describe("#2187 — pk-lose Create-Replay-Klassifikation", () => {
    it("markiert einen 422-Create als dead und meldet Feldfehler", async () => {
        const errors = { occurred_at_fold: [{ message: "Bitte Sommer- oder Winterzeit wählen.", code: "fold_required" }] };
        const expectedErrorCodes = { occurred_at_fold: ["fold_required"] };
        vi.stubGlobal(
            "fetch",
            vi.fn(async () => ({
                redirected: false,
                status: 422,
                json: async () => ({ error: "invalid", errors: errors }),
            })),
        );
        const record = {
            pk: eventPk,
            localStatus: "new",
            data: {
                occurredAt: "2026-10-25T02:30",
                formData: {},
                idempotencyKey: "idem-1",
            },
        };

        const result = await window.offlineEdit.replayModifiedEvent(record);

        expect(result).toEqual({ status: "dead", deadReason: "invalid", errors: expectedErrorCodes, statusCode: 422 });
        expect(offlineStore.markEventDead).toHaveBeenCalledWith(eventPk, "invalid", JSON.stringify(expectedErrorCodes));
        expect(offlineStore.updateEventLocalStatus).not.toHaveBeenCalledWith(eventPk, "new");
    });

    it("bleibt fuer 404/410 Create unchanged dead-lettering", async () => {
        for (const status of [404, 410]) {
            vi.clearAllMocks();
            vi.stubGlobal(
                "fetch",
                vi.fn(async () => ({
                    redirected: false,
                    status,
                    json: async () => ({}),
                })),
            );
            const record = { pk: eventPk, localStatus: "new", data: { formData: {}, idempotencyKey: "idem-2" } };
            const result = await window.offlineEdit.replayModifiedEvent(record);
            expect(result).toEqual({ status: "dead", deadReason: "not-found", statusCode: status });
            expect(offlineStore.markEventDead).toHaveBeenCalledWith(eventPk, "not-found", String(status));
        }
    });
});
