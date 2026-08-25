// Refs #1519/#1828: Reine (this-freie) Helfer der Offline-Formularschicht
// (window.offlineFormFields). Die Feld-Helfer (fieldDescriptor/initialValue/
// buildFormData) waren bisher nur indirekt abgedeckt; die mit #1828 hierher
// konsolidierten frueheren Spiegel-Paare aus offline-client-view.js und
// offline-create.js (Datums-Inputs, WorkItem-Defaults, Datei-Marker-Text)
// werden direkt gepinnt.
import { describe, it, expect, beforeAll, afterEach, vi } from "vitest";

let helpers;

beforeAll(async () => {
    await import("../../src/static/js/offline-form-fields.js");
    helpers = window.offlineFormFields;
});

afterEach(() => {
    vi.useRealTimers();
});

describe("Datums-Input-Helfer (frueher Spiegel-Paare, Refs #1323/#1398)", () => {
    it("nowLocalInput liefert den datetime-local-Wert mit Padding", () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date(2026, 7, 5, 9, 4));
        expect(helpers.nowLocalInput()).toBe("2026-08-05T09:04");
    });

    it("todayInput liefert das heutige Datum als date-Input-Wert", () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date(2026, 0, 31, 23, 59));
        expect(helpers.todayInput()).toBe("2026-01-31");
    });

    it("maxWorkItemDateInput liefert den 31.12. des Folgejahrs", () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date(2026, 7, 25));
        expect(helpers.maxWorkItemDateInput()).toBe("2027-12-31");
    });
});

describe("WorkItem-Form-Helfer (Feldnamen 1:1 zur Server-WorkItemForm)", () => {
    it("emptyWorkItemValues traegt exakt die WorkItemForm-Defaults", () => {
        expect(helpers.emptyWorkItemValues()).toEqual({
            item_type: "task",
            title: "",
            description: "",
            priority: "normal",
            due_date: "",
            remind_at: "",
            recurrence: "none",
            assigned_to: "",
        });
    });

    it("emptyWorkItemValues liefert pro Aufruf ein frisches Objekt", () => {
        expect(helpers.emptyWorkItemValues()).not.toBe(helpers.emptyWorkItemValues());
    });

    it("workItemFormData uebernimmt genau die acht Formularfelder", () => {
        const wiValues = {
            item_type: "hint",
            title: "T",
            description: "D",
            priority: "urgent",
            due_date: "2026-09-01",
            remind_at: "2026-08-30",
            recurrence: "none",
            assigned_to: "u-1",
            editing: true, // Komponenten-Zustand darf NICHT in den Replay-POST
        };
        expect(helpers.workItemFormData(wiValues)).toEqual({
            item_type: "hint",
            title: "T",
            description: "D",
            priority: "urgent",
            due_date: "2026-09-01",
            remind_at: "2026-08-30",
            recurrence: "none",
            assigned_to: "u-1",
        });
    });
});

describe("fileNote (Datei-Marker-Anzeigetext)", () => {
    it("einzelne Datei mit/ohne Namen", () => {
        expect(helpers.fileNote({ __file__: true, name: "a.pdf" })).toBe("Datei vorhanden (a.pdf)");
        expect(helpers.fileNote({ __file__: true })).toBe("Datei vorhanden");
    });

    it("Mehrfach-Marker mit Zaehler", () => {
        expect(helpers.fileNote({ __files__: true, count: 3 })).toBe("Dateien vorhanden (3)");
        expect(helpers.fileNote({ __files__: true })).toBe("Dateien vorhanden (0)");
    });

    it("kein Marker -> 'Keine Datei'", () => {
        expect(helpers.fileNote(null)).toBe("Keine Datei");
        expect(helpers.fileNote(undefined)).toBe("Keine Datei");
    });
});

describe("Feld-Helfer (Refs #1519, bisher nur indirekt abgedeckt)", () => {
    const fileField = { slug: "anhang", name: "Anhang", field_type: "file" };

    it("fieldDescriptor ruft fileNote nur fuer FILE-Felder", () => {
        const ev = { data_fields: { anhang: { __file__: true, name: "x" } } };
        const desc = helpers.fieldDescriptor(fileField, ev, helpers.fileNote);
        expect(desc.isFile).toBe(true);
        expect(desc.fileNote).toBe("Datei vorhanden (x)");

        const textDesc = helpers.fieldDescriptor(
            { slug: "notiz", name: "Notiz", field_type: "text" },
            { data_fields: {} },
            helpers.fileNote
        );
        expect(textDesc.isFile).toBe(false);
        expect(textDesc.fileNote).toBe("");
        expect(textDesc.isPlainInput).toBe(true);
        expect(textDesc.inputType).toBe("text");
    });

    it("initialValue normalisiert boolean/multi_select/Objekt-Marker", () => {
        expect(helpers.initialValue("boolean", "true")).toBe(true);
        expect(helpers.initialValue("boolean", "0")).toBe(false);
        expect(helpers.initialValue("multi_select", ["a"])).toEqual(["a"]);
        expect(helpers.initialValue("multi_select", "a")).toEqual(["a"]);
        expect(helpers.initialValue("multi_select", "")).toEqual([]);
        expect(helpers.initialValue("text", { __file__: true })).toBe("");
        expect(helpers.initialValue("text", 5)).toBe("5");
    });

    it("buildFormData ueberspringt FILE-Felder", () => {
        const fields = [
            { slug: "notiz", isFile: false },
            { slug: "anhang", isFile: true },
        ];
        expect(helpers.buildFormData(fields, { notiz: "n", anhang: "x" })).toEqual({ notiz: "n" });
    });
});
