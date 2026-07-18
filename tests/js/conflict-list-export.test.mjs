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
