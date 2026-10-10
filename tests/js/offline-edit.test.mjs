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
        // Replay classification assumes a previously validated revocation list.
        isRevocationPending: async () => false,
        updateEventLocalStatus: vi.fn(async () => {}),
        saveConflictState: vi.fn(async () => {}),
        countUnsyncedEvents: vi.fn(async () => 1),
        countConflictEvents: vi.fn(async () => 0),
        countDeadEvents: vi.fn(async () => 0),
    };
    window.offlineStore = offlineStore;
    await import("../../src/static/js/offline-form-fields.js");
    await import("../../src/static/js/offline-edit.js");
});

describe("Selection arrays survive an offline edit and reconnect replay", () => {
    it.each([[], ["a"], ["b", "a"], ["a", "b", "a"], ["comma,value", "quote'value"]])(
        "sends repeated form keys without converting the stored array for %j",
        async (...values) => {
            let saved;
            offlineStore.getOfflineEvent = vi.fn(async () => saved || null);
            offlineStore.saveOfflineEdit = vi.fn(async (record) => {
                saved = JSON.parse(JSON.stringify(record));
            });
            const helpers = window.offlineFormFields;
            const initial = helpers.initialValue("multi_select", values);
            const formData = helpers.buildFormData([{ slug: "selection", isMultiSelect: true }], { selection: initial });
            const token = "2026-10-09T10:00:00+00:00";
            const record = await window.offlineEdit.markEventModified(eventPk, formData, { expectedUpdatedAt: token });
            expect(saved.data.formData.selection).toEqual(values);
            expect(Array.isArray(saved.data.formData.selection)).toBe(true);

            vi.stubGlobal("fetch", vi.fn(async () => ({
                status: 200,
                redirected: false,
                headers: new Headers({ "X-Offline-Replay-Result": "saved" }),
            })));
            Object.defineProperty(window.navigator, "onLine", { configurable: true, value: false });
            try {
                expect(await window.offlineEdit.replayModifiedEvent(saved)).toEqual({ status: "offline" });
                expect(fetch).not.toHaveBeenCalled();
            } finally {
                Object.defineProperty(window.navigator, "onLine", { configurable: true, value: true });
            }

            expect(await window.offlineEdit.replayModifiedEvent(saved)).toEqual({ status: "synced" });
            expect(fetch).toHaveBeenCalledTimes(1);
            const [url, request] = fetch.mock.calls[0];
            expect(url).toBe(`/events/${eventPk}/edit/`);
            expect(request.headers.Accept).toBe("application/json");
            const body = new URLSearchParams(request.body);
            expect(body.getAll("selection")).toEqual(values);
            expect(body.get("expected_updated_at")).toBe(token);
            expect(saved.data.formData.selection).toEqual(values);
            expect(record.data.formData.selection).toEqual(values);
            expect(offlineStore.updateEventLocalStatus).toHaveBeenCalledWith(eventPk, "clean");
        },
    );
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
