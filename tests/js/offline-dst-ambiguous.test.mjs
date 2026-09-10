// Refs #2187: clientseitige Erkennung der doppelten Wanduhrstunde.
import { beforeAll, describe, expect, it, vi } from "vitest";

let helpers;
let eventCreateFactory;

beforeAll(async () => {
    await import("../../src/static/js/offline-form-fields.js");
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

        expect(component.dstError).toContain("zweimal");
        expect(markEventNew).not.toHaveBeenCalled();
        expect(document.activeElement).toBe(occurredAt);
        root.remove();
    });
});
