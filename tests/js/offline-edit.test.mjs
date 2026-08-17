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

describe("#1936 — Offline-Edit-Validierungsfehler", () => {
    it("behaelt einen 422-Edit als modified und meldet ihn als invalid", async () => {
        const errors = {
            dauer: [{ message: "Wert darf höchstens 10 sein.", code: "" }],
        };
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
            localStatus: "modified",
            data: {
                formData: { dauer: "11" },
                expectedUpdatedAt: "2026-08-17T10:00:00+00:00",
            },
        };

        const result = await window.offlineEdit.replayModifiedEvent(record);

        expect(result).toEqual({ status: "invalid", errors: errors });
        expect(offlineStore.updateEventLocalStatus).toHaveBeenCalledWith(eventPk, "modified");
        expect(offlineStore.saveConflictState).not.toHaveBeenCalled();
        expect(fetch.mock.calls[0][1].headers.Accept).toBe("application/json");
    });
});
