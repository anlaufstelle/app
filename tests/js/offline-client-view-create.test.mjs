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
