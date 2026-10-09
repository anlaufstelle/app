// Refs #2187: Die Offline-Erfassung innerhalb einer mitgenommenen Akte nutzt
// denselben DST-Guard und behält synchrone 422-Feldfehler als invalid.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

let factory;

beforeAll(async () => {
    window.URL_PATTERNS = { extractClientPk: () => "client-1" };
    window.Alpine = {
        data: (name, fn) => {
            if (name === "offlineClientView") factory = fn;
        },
    };
    await import("../../src/static/js/offline-form-fields.js");
    await import("../../src/static/js/offline-messages.js");
    await import("../../src/static/js/offline-client-view.js");
    document.dispatchEvent(new Event("alpine:init"));
});

function componentRoot() {
    const root = document.createElement("div");
    root.innerHTML = `
        <form data-app-timezone="Europe/Berlin">
            <input data-testid="offline-create-occurred-at">
        </form>`;
    document.body.append(root);
    return root;
}

function makeComponent(root) {
    const component = factory();
    component.$el = root;
    component._pk = "client-1";
    component.data = { documentTypes: [], cases: [], events: [], workitems: [] };
    component.load = vi.fn(async () => {});
    component._reconcile = vi.fn(async () => {});
    return component;
}

beforeEach(() => {
    Object.defineProperty(window.navigator, "onLine", { configurable: true, value: false });
});

describe("Offline-Akten-Create", () => {
    it("weist die doppelte Stunde vor dem lokalen Speichern ab und fokussiert das Feld", async () => {
        const root = componentRoot();
        const component = makeComponent(root);
        component.createDocTypePk = "doctype-1";
        component.createOccurredAt = "2026-10-25T02:30";
        const markEventNew = vi.fn(async () => ({}));
        window.offlineEdit = { markEventNew };

        await component.saveCreate();

        expect(component.dstError).toBe(window.offlineFormFields.dstAmbiguousMessage);
        expect(markEventNew).not.toHaveBeenCalled();
        expect(document.activeElement).toBe(root.querySelector('[data-testid="offline-create-occurred-at"]'));
        root.remove();
    });

    // Refs #2187: ``@submit.prevent="saveCreate"`` steht auf dem Formular —
    // Alpine liefert es als ``$el``, und genau dort sitzt auch
    // ``data-app-timezone`` (Gegenstueck zur Spec in
    // tests/js/offline-dst-ambiguous.test.mjs).
    it("weist die doppelte Stunde auch ab, wenn Alpine das Formular als $el liefert", async () => {
        const root = componentRoot();
        const component = makeComponent(root);
        component.$el = root.querySelector("form");
        component.createDocTypePk = "doctype-1";
        component.createOccurredAt = "2026-10-25T02:30";
        const markEventNew = vi.fn(async () => ({}));
        window.offlineEdit = { markEventNew };

        await component.saveCreate();

        expect(component.dstError).toContain("zweimal");
        expect(markEventNew).not.toHaveBeenCalled();
        expect(document.activeElement).toBe(root.querySelector('[data-testid="offline-create-occurred-at"]'));
        root.remove();
    });

    it("zeigt beim synchronen 422 die Feldfehler als invalid", async () => {
        Object.defineProperty(window.navigator, "onLine", { configurable: true, value: true });
        const root = componentRoot();
        const component = makeComponent(root);
        component.createDocTypePk = "doctype-1";
        component.createOccurredAt = "2026-10-25T03:30";
        const record = { pk: "event-new", localStatus: "new", data: {} };
        const errors = { dauer: [{ message: "Wert ist ungültig.", code: "invalid" }] };
        window.offlineEdit = {
            markEventNew: vi.fn(async () => record),
            replayModifiedEvent: vi.fn(async () => ({ status: "invalid", errors })),
        };

        await component.saveCreate();

        expect(window.offlineEdit.replayModifiedEvent).toHaveBeenCalledWith(record);
        expect(component.lastSyncResult).toBe("invalid");
        expect(component.editError).toBe("Wert ist ungültig.");
        expect(component.showDead).toBe(false);
        root.remove();
    });
});


describe("Inherited field slugs in offline editing", () => {
    it.each(["constructor", "__proto__", "toString", "hasOwnProperty"])("preserves %s when editing and creating", async (slug) => {
        const root = componentRoot();
        const component = makeComponent(root);
        const field = { slug, name: "Selection", field_type: "multi_select" };
        component.data.documentTypes = [{ pk: "doctype-1", fields: [field] }];
        const event = { pk: "event-1", document_type_pk: "doctype-1", data_fields: { [slug]: ["a", "b", "a"] } };
        component.startEdit(event);
        expect(JSON.parse(JSON.stringify(component.editValues))).toEqual({ [slug]: ["a", "b", "a"] });
        window.offlineEdit = { markEventModified: vi.fn(async () => ({ pk: event.pk })) };
        await component.saveEdit(event);
        expect(JSON.parse(JSON.stringify(window.offlineEdit.markEventModified.mock.calls[0][1]))).toEqual({ [slug]: ["a", "b", "a"] });
        component.startEdit({ ...event, data_fields: {} });
        expect(component.editValues[slug]).toEqual([]);
        component.createDocTypePk = "doctype-1";
        component.onCreateDocTypeChange();
        expect(JSON.parse(JSON.stringify(component.editValues))).toEqual({ [slug]: [] });
        root.remove();
    });
});
