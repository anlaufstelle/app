// Refs #1476 (R10): Der Offline-Notiz-Export (conflict-list.js exportNote)
// schreibt entschluesselte Art.-9-PII als Klartext-Datei — er muss eine
// BEWUSSTE, protokollierte Aktion sein: erst Bestaetigung/Hinweis, dann
// Audit-Meldung (best-effort ueber die Offline-Replay-Queue), dann Download.
//
// conflict-list.js registriert die Alpine-Komponente ueber
// ``Alpine.data("conflictList", factory)`` im ``alpine:init``-Event. Wir fangen
// die Factory mit einem Alpine-Stub ab und treiben ``exportNote`` direkt.
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from "vitest";

let factory;

beforeAll(async () => {
    window.Alpine = {
        data: (name, fn) => {
            if (name === "conflictList") factory = fn;
        },
    };
    // Refs #1828: der Audit-Endpunkt kommt aus url-patterns.js (ENDPOINTS) —
    // wie auf der echten Seite (offline_base.html) VOR conflict-list.js laden.
    await import("../../src/static/js/url-patterns.js");
    // Refs #1828: die gemeinsamen Formatter kommen aus offline-form-fields.js —
    // wie im Template VOR conflict-list.js laden.
    await import("../../src/static/js/offline-form-fields.js");
    await import("../../src/static/js/conflict-list.js");
    document.dispatchEvent(new Event("alpine:init"));
    // URL.createObjectURL fehlt in jsdom -> stubben.
    window.URL.createObjectURL = vi.fn(() => "blob:stub");
    window.URL.revokeObjectURL = vi.fn();
});

function makeComponent() {
    const c = factory();
    c._confirmExportText = "warn";
    c._labelExported = "done";
    c._labelActionFailed = "fail";
    return c;
}

let enqueue;

beforeEach(() => {
    enqueue = vi.fn(() => Promise.resolve());
    window.offlineQueue = { enqueueRequest: enqueue };
    window.URL.createObjectURL.mockClear();
    window.URL.revokeObjectURL.mockClear();
    vi.spyOn(window.HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});

afterEach(() => {
    delete window.offlineQueue;
    vi.restoreAllMocks();
});

describe("R10 — exportNote als bewusste, protokollierte Aktion", () => {
    it("bricht ohne Bestaetigung ab: kein Audit, kein Download", () => {
        vi.spyOn(window, "confirm").mockReturnValue(false);
        const c = makeComponent();
        c.exportNote({ pk: "ev-1", noteText: "PII" });
        expect(enqueue).not.toHaveBeenCalled();
        expect(window.URL.createObjectURL).not.toHaveBeenCalled();
    });

    it("nach Bestaetigung: meldet den Export ans Audit-Endpoint und laedt herunter", () => {
        vi.spyOn(window, "confirm").mockReturnValue(true);
        const c = makeComponent();
        c.exportNote({ pk: "ev-1", noteText: "PII" });

        expect(enqueue).toHaveBeenCalledTimes(1);
        const [url, method, body, headers] = enqueue.mock.calls[0];
        expect(url).toBe("/api/v1/offline/audit/note-export/");
        expect(method).toBe("POST");
        expect(JSON.parse(body)).toEqual({ event_pk: "ev-1" });
        expect(headers["content-type"]).toBe("application/json");
        expect(headers["hx-request"]).toBe("true");

        expect(window.URL.createObjectURL).toHaveBeenCalledTimes(1);
        expect(c.feedback).toBe("done");
    });

    it("exportiert auch, wenn die Audit-Meldung fehlschlaegt (best-effort)", async () => {
        vi.spyOn(window, "confirm").mockReturnValue(true);
        window.offlineQueue = { enqueueRequest: vi.fn(() => Promise.reject(new Error("boom"))) };
        const c = makeComponent();
        c.exportNote({ pk: "ev-2", noteText: "PII" });
        await Promise.resolve();
        expect(window.URL.createObjectURL).toHaveBeenCalledTimes(1);
        expect(c.feedback).toBe("done");
    });

    it("exportiert auch ohne geladene Offline-Queue", () => {
        vi.spyOn(window, "confirm").mockReturnValue(true);
        delete window.offlineQueue;
        const c = makeComponent();
        c.exportNote({ pk: "ev-3", noteText: "PII" });
        expect(window.URL.createObjectURL).toHaveBeenCalledTimes(1);
        expect(c.feedback).toBe("done");
    });
});

describe("#2187 — PII-freie Feldcodes im Dead-Letter-View-Model", () => {
    function deadItem(lastError) {
        const c = makeComponent();
        c._reasonText = { invalid: "Vom Server als ungültig abgelehnt" };
        return c._eventItem(
            {
                pk: "ev-dead",
                data: {
                    deadReason: "invalid",
                    documentTypeName: "Kontakt",
                    formData: {},
                    lastError,
                },
            },
            true,
        );
    }

    it("gibt Feldnamen und Fehlercodes fuer die Anzeige frei", () => {
        const item = deadItem(JSON.stringify({ occurred_at_fold: ["fold_required"], dauer: ["min_value"] }));
        expect(item.reasonText).toBe("Vom Server als ungültig abgelehnt");
        expect(item.fieldErrors).toEqual([
            { key: "occurred_at_fold", text: "occurred_at_fold (fold_required)" },
            { key: "dauer", text: "dauer (min_value)" },
        ]);
        expect(item.hasFieldErrors).toBe(true);
    });

    it("laesst Dead-Events ohne lastError generisch", () => {
        const item = deadItem("");
        expect(item.fieldErrors).toEqual([]);
        expect(item.hasFieldErrors).toBe(false);
    });

    it.each([
        "kein-json",
        JSON.stringify({ occurred_at_fold: [{ code: "fold_required", value: "02:30" }] }),
        JSON.stringify({ occurred_at_fold: ["fold_required", "Wert 02:30"] }),
        JSON.stringify(["fold_required"]),
    ])("zeigt bei unerwartetem lastError-Inhalt fail-closed nichts an", (lastError) => {
        const item = deadItem(lastError);
        expect(item.fieldErrors).toEqual([]);
        expect(item.hasFieldErrors).toBe(false);
    });
});

// Refs #1828: Verhaltens-Pin der fuenf Listen-Aktionen VOR der Konsolidierung
// auf das gemeinsame _act-Skelett — nur "Verwerfen" ist confirm-pflichtig
// (S1-Regel im Dateikopf), jede Aktion laedt danach neu, frischt die
// Banner-Zaehler auf und meldet Erfolg/Fehler in der Feedback-Zeile.
describe("#1828 — Aktions-Skelett retry/discard/reapply", () => {
    let store;
    let refreshQueueCount;
    let refreshEditCounts;

    function makeActionComponent() {
        const c = factory();
        c._confirmDiscardText = "wirklich?";
        c._labelRetried = "retried";
        c._labelDiscarded = "discarded";
        c._labelActionFailed = "failed";
        return c;
    }

    beforeEach(() => {
        store = {
            listConflicts: vi.fn(async () => []),
            listDeadEvents: vi.fn(async () => []),
            listQueueEntries: vi.fn(async () => []),
            retryDeadEvent: vi.fn(async () => {}),
            discardDeadEvent: vi.fn(async () => {}),
            retryQueueEntry: vi.fn(async () => {}),
            reapplyQueueEntryWithServerVersion: vi.fn(async () => {}),
            discardQueueEntry: vi.fn(async () => {}),
        };
        window.offlineStore = store;
        refreshQueueCount = vi.fn();
        refreshEditCounts = vi.fn();
        window.offlineQueue = { refreshCount: refreshQueueCount };
        window.offlineEdit = { refreshCounts: refreshEditCounts };
        delete window.syncOrchestrator;
    });

    afterEach(() => {
        delete window.offlineStore;
        delete window.offlineEdit;
        delete window.syncOrchestrator;
    });

    it("retryDeadEvent: kein confirm, Erfolg -> reload + Zaehler + retried", async () => {
        const confirmSpy = vi.spyOn(window, "confirm");
        const c = makeActionComponent();
        await c.retryDeadEvent("ev-1");

        expect(confirmSpy).not.toHaveBeenCalled();
        expect(store.retryDeadEvent).toHaveBeenCalledWith("ev-1");
        expect(store.listConflicts).toHaveBeenCalled();
        expect(refreshQueueCount).toHaveBeenCalled();
        expect(refreshEditCounts).toHaveBeenCalled();
        expect(c.feedback).toBe("retried");
        expect(c.feedbackType).toBe("info");
    });

    it("discardDeadEvent: confirm-Abbruch tut nichts", async () => {
        vi.spyOn(window, "confirm").mockReturnValue(false);
        const c = makeActionComponent();
        await c.discardDeadEvent("ev-1");

        expect(store.discardDeadEvent).not.toHaveBeenCalled();
        expect(c.feedback).toBe("");
    });

    it("discardQueueEntry: nach confirm -> discarded-Feedback", async () => {
        vi.spyOn(window, "confirm").mockReturnValue(true);
        const c = makeActionComponent();
        await c.discardQueueEntry(7);

        expect(store.discardQueueEntry).toHaveBeenCalledWith(7);
        expect(c.feedback).toBe("discarded");
        expect(c.feedbackType).toBe("info");
    });

    it("Fehler in der Aktion -> ActionFailed-Feedback (error)", async () => {
        store.retryQueueEntry.mockRejectedValue(new Error("boom"));
        const c = makeActionComponent();
        await c.retryQueueEntry(7);

        expect(c.feedback).toBe("failed");
        expect(c.feedbackType).toBe("error");
    });

    it("reapplyQueueEntry: reaktiviert die Row und stoesst requestSync an", async () => {
        const requestSync = vi.fn();
        window.syncOrchestrator = { requestSync };
        const c = makeActionComponent();
        await c.reapplyQueueEntry(9);

        expect(store.reapplyQueueEntryWithServerVersion).toHaveBeenCalledWith(9);
        expect(requestSync).toHaveBeenCalled();
        expect(c.feedback).toBe("retried");
    });

    it("reapplyQueueEntry ohne Orchestrator: faellt auf replayQueue zurueck", async () => {
        const replayQueue = vi.fn(async () => {});
        window.offlineQueue = { refreshCount: refreshQueueCount, replayQueue };
        const c = makeActionComponent();
        await c.reapplyQueueEntry(9);

        expect(replayQueue).toHaveBeenCalled();
        expect(c.feedback).toBe("retried");
    });
});
