// Refs #2187: clientseitige Erkennung der doppelten Wanduhrstunde.
import { beforeAll, describe, expect, it, vi } from "vitest";

let helpers;
let eventCreateFactory;

beforeAll(async () => {
    await import("../../src/static/js/offline-form-fields.js");
    await import("../../src/static/js/offline-messages.js");
    helpers = window.offlineFormFields;
    const factories = {};
    globalThis.Alpine = {
        data: (name, factory) => {
            factories[name] = factory;
        },
    };
    await import("../../src/static/js/offline-create.js");
    document.dispatchEvent(new Event("alpine:init"));
    eventCreateFactory = factories.offlineEventCreate;
});

describe("isAmbiguousWallClock", () => {
    it("liefert den zentralen DST-Hinweis unveraendert", () => {
        expect(helpers.dstAmbiguousMessage).toBe(
            "Diese Uhrzeit gibt es am Tag der Zeitumstellung zweimal. Bitte online erfassen oder eine Uhrzeit außerhalb von 02:00–02:59 wählen.",
        );
    });

    it("erkennt 02:30 am Rueckfalltag Europe/Berlin als mehrdeutig", () => {
        expect(helpers.isAmbiguousWallClock("2026-10-25T02:30", "Europe/Berlin")).toBe(true);
    });

    it("erkennt 02:00 am Rueckfalltag Europe/Berlin als mehrdeutig", () => {
        expect(helpers.isAmbiguousWallClock("2026-10-25T02:00", "Europe/Berlin")).toBe(true);
    });

    it("erkennt 03:30 am Rueckfalltag Europe/Berlin als eindeutig", () => {
        expect(helpers.isAmbiguousWallClock("2026-10-25T03:30", "Europe/Berlin")).toBe(false);
    });

    it("erkennt 01:30 am Rueckfalltag Europe/Berlin als eindeutig", () => {
        expect(helpers.isAmbiguousWallClock("2026-10-25T01:30", "Europe/Berlin")).toBe(false);
    });

    it("erkennt 02:30 am Vorstelltag Europe/Berlin als nicht existent (nicht mehrdeutig)", () => {
        expect(helpers.isAmbiguousWallClock("2026-03-29T02:30", "Europe/Berlin")).toBe(false);
    });

    it("ist an normalen Tagen eindeutig", () => {
        expect(helpers.isAmbiguousWallClock("2026-08-10T14:00", "Europe/Berlin")).toBe(false);
    });

    it.each([
        ["America/New_York", "2026-11-01T01:30", "2026-11-01T02:30"],
        ["Australia/Sydney", "2026-04-05T02:30", "2026-04-05T03:30"],
        ["Pacific/Auckland", "2026-04-05T02:30", "2026-04-05T03:30"],
    ])("erkennt die Rueckfallstunde in %s", (timeZone, ambiguous, unambiguous) => {
        expect(helpers.isAmbiguousWallClock(ambiguous, timeZone)).toBe(true);
        expect(helpers.isAmbiguousWallClock(unambiguous, timeZone)).toBe(false);
    });

    it("akzeptiert Sekunden im datetime-local-Wert", () => {
        expect(helpers.isAmbiguousWallClock("2026-10-25T02:30:15", "Europe/Berlin")).toBe(true);
    });

    it("bleibt bei fehlender Intl-Unterstuetzung fail-open", () => {
        const dateTimeFormat = vi.spyOn(Intl, "DateTimeFormat").mockImplementation(function () {
            throw new RangeError("longOffset unsupported");
        });
        expect(helpers.isAmbiguousWallClock("2026-10-25T02:30", "Europe/Berlin")).toBe(false);
        dateTimeFormat.mockRestore();
    });

    it("speichert eine mehrdeutige Wanduhrzeit nicht und setzt den Fokus zurueck", async () => {
        const root = document.createElement("div");
        root.innerHTML = `
            <form data-app-timezone="Europe/Berlin">
                <input data-testid="offline-event-create-occurred-at">
            </form>`;
        document.body.append(root);
        const occurredAt = root.querySelector('[data-testid="offline-event-create-occurred-at"]');
        const markEventNew = vi.fn(async () => {});
        window.offlineEdit = { markEventNew };
        const component = eventCreateFactory();
        component.$el = root;
        component.createOccurredAt = "2026-10-25T02:30";

        await component.saveCreate();

        expect(component.dstError).toBe(helpers.dstAmbiguousMessage);
        expect(markEventNew).not.toHaveBeenCalled();
        expect(document.activeElement).toBe(occurredAt);
        root.remove();
    });

    // Refs #2187: Alpine bindet ``$el`` an das Element, auf dem der Ausdruck
    // steht — ``@submit.prevent="saveCreate"`` sitzt auf dem FORMULAR, und
    // ebendieses Formular traegt ``data-app-timezone``. Ein reines
    // ``querySelector`` (nur Nachfahren) findet das Attribut dann nie, faellt
    // still auf "UTC" zurueck und die Rueckfallstunde rutscht durch.
    it("erkennt die doppelte Stunde auch, wenn Alpine das Formular als $el liefert", async () => {
        const root = document.createElement("div");
        root.innerHTML = `
            <form data-app-timezone="Europe/Berlin">
                <input data-testid="offline-event-create-occurred-at">
            </form>`;
        document.body.append(root);
        const form = root.querySelector("form");
        const occurredAt = root.querySelector('[data-testid="offline-event-create-occurred-at"]');
        const markEventNew = vi.fn(async () => ({}));
        window.offlineEdit = { markEventNew };
        const component = eventCreateFactory();
        component.$el = form;
        component.createDocTypePk = "doctype-1";
        component.createOccurredAt = "2026-10-25T02:30";

        await component.saveCreate();

        expect(component.dstError).toContain("zweimal");
        expect(markEventNew).not.toHaveBeenCalled();
        expect(component.saved).not.toBe(true);
        expect(document.activeElement).toBe(occurredAt);
        root.remove();
    });

    it("setzt den Save bei fehlender Intl-Unterstuetzung fort", async () => {
        const root = document.createElement("div");
        root.innerHTML = `
            <form data-app-timezone="Europe/Berlin">
                <input data-testid="offline-event-create-occurred-at">
            </form>`;
        document.body.append(root);
        const markEventNew = vi.fn(async () => ({}));
        window.offlineEdit = { markEventNew };
        const component = eventCreateFactory();
        component.$el = root;
        component.createDocTypePk = "doctype-1";
        component.createOccurredAt = "2026-10-25T02:30";
        const dateTimeFormat = vi.spyOn(Intl, "DateTimeFormat").mockImplementation(function () {
            throw new RangeError("longOffset unsupported");
        });

        await component.saveCreate();

        expect(markEventNew).toHaveBeenCalledTimes(1);
        expect(component.saved).toBe(true);
        expect(component.dstError).toBe("");
        dateTimeFormat.mockRestore();
        root.remove();
    });
});


describe("Own field values in standalone offline creation", () => {
    it.each(["constructor", "__proto__", "toString", "hasOwnProperty"])("preserves the inherited field slug %s in submitted JSON", (slug) => {
        const component = eventCreateFactory();
        component.facility = { documentTypes: [{ pk: "doctype-1", fields: [{ slug, field_type: "multi_select" }] }] };
        component.createDocTypePk = "doctype-1";
        component.onCreateDocTypeChange();
        expect(JSON.parse(JSON.stringify(component.editValues))).toEqual({ [slug]: [] });
        component.editValues[slug] = ["a", "b", "a"];
        expect(JSON.parse(JSON.stringify(helpers.buildFormData(component.editFields, component.editValues)))).toEqual({ [slug]: ["a", "b", "a"] });
    });
});
