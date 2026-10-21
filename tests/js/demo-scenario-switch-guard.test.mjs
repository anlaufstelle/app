// Refs #2285 (Fix-Runde 1, Ticket-Punkt 9): Warnung vor ungespeicherter
// Offline-Arbeit beim Demo-Start/-Wechsel. Zwei Faelle: ohne ausstehende
// Eintraege direkter Wechsel, mit ausstehenden Eintraegen eine Bestaetigung
// mit Anzahl (Abbruch bei "Abbrechen").
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

let guard;

beforeEach(() => {
    vi.useFakeTimers();
});

afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
});

beforeAll(async () => {
    await import("../../src/static/js/demo-scenario-switch-guard.js");
    guard = window.demoScenarioSwitchGuard;
});

function makeForm() {
    document.body.innerHTML =
        '<form data-demo-scenario-form><input type="hidden" name="scenario" value="kontaktladen"></form>';
    document.body.setAttribute(
        "data-i18n-demo-switch-confirm",
        "{count} Änderungen sind noch nicht übertragen und gehen beim Wechsel verloren. Trotzdem wechseln?"
    );
    const form = document.querySelector("[data-demo-scenario-form]");
    form.requestSubmit = vi.fn();
    form.submit = vi.fn();
    return form;
}

async function flushMicrotasks() {
    await vi.runAllTimersAsync();
}

describe("unsyncedEntryCount", () => {
    afterEach(() => {
        delete window.offlineStore;
    });

    it("liefert 0 ohne window.offlineStore (Fail-Open, z. B. anonymer Erstbesuch)", async () => {
        expect(await guard.unsyncedEntryCount()).toBe(0);
    });

    it("summiert countUnsyncedEvents + countQueueByStatus.total", async () => {
        window.offlineStore = {
            countUnsyncedEvents: async () => 2,
            countQueueByStatus: async () => ({ total: 3, pending: 3, blocked: 0 }),
        };
        expect(await guard.unsyncedEntryCount()).toBe(5);
    });

    it("behandelt fehlende Teilfunktionen als 0", async () => {
        window.offlineStore = {};
        expect(await guard.unsyncedEntryCount()).toBe(0);
    });
});

describe("attachGuard — Fall A: keine ausstehenden Eintraege", () => {
    beforeEach(() => {
        window.offlineStore = {
            countUnsyncedEvents: async () => 0,
            countQueueByStatus: async () => ({ total: 0, pending: 0, blocked: 0 }),
        };
    });
    afterEach(() => {
        delete window.offlineStore;
    });

    it("sendet das Formular direkt ab, ohne confirm() aufzurufen", async () => {
        const form = makeForm();
        const confirmSpy = vi.spyOn(window, "confirm");
        guard.attachGuard(form);

        form.dispatchEvent(new Event("submit", { cancelable: true }));
        await flushMicrotasks();

        expect(confirmSpy).not.toHaveBeenCalled();
        expect(form.requestSubmit).toHaveBeenCalledTimes(1);
        confirmSpy.mockRestore();
    });
});

describe("attachGuard — Fall B: ausstehende Eintraege vorhanden", () => {
    beforeEach(() => {
        window.offlineStore = {
            countUnsyncedEvents: async () => 1,
            countQueueByStatus: async () => ({ total: 2, pending: 2, blocked: 0 }),
        };
    });
    afterEach(() => {
        delete window.offlineStore;
    });

    it("fragt mit der Anzahl (3) bestaetigt und sendet danach ab", async () => {
        const form = makeForm();
        const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
        guard.attachGuard(form);

        form.dispatchEvent(new Event("submit", { cancelable: true }));
        await flushMicrotasks();

        expect(confirmSpy).toHaveBeenCalledWith(
            "3 Änderungen sind noch nicht übertragen und gehen beim Wechsel verloren. Trotzdem wechseln?"
        );
        expect(form.requestSubmit).toHaveBeenCalledTimes(1);
        confirmSpy.mockRestore();
    });

    it("bricht ab (kein Submit), wenn confirm() abgelehnt wird", async () => {
        const form = makeForm();
        const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
        guard.attachGuard(form);

        form.dispatchEvent(new Event("submit", { cancelable: true }));
        await flushMicrotasks();

        expect(confirmSpy).toHaveBeenCalledTimes(1);
        expect(form.requestSubmit).not.toHaveBeenCalled();
        confirmSpy.mockRestore();
    });

    it("faellt bei fehlendem data-i18n-demo-switch-confirm auf einen generischen Text zurueck", async () => {
        const form = makeForm();
        document.body.removeAttribute("data-i18n-demo-switch-confirm");
        const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
        guard.attachGuard(form);

        form.dispatchEvent(new Event("submit", { cancelable: true }));
        await flushMicrotasks();

        expect(confirmSpy).toHaveBeenCalledTimes(1);
        const [msg] = confirmSpy.mock.calls[0];
        expect(msg).toContain("3");
        expect(msg.length).toBeGreaterThan(0);
        expect(form.requestSubmit).toHaveBeenCalledTimes(1);
        confirmSpy.mockRestore();
    });

    it("faellt bei leerem data-i18n-demo-switch-confirm ebenfalls auf den generischen Text zurueck (kein stiller Fail-Open)", async () => {
        const form = makeForm();
        document.body.setAttribute("data-i18n-demo-switch-confirm", "");
        const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
        guard.attachGuard(form);

        form.dispatchEvent(new Event("submit", { cancelable: true }));
        await flushMicrotasks();

        // confirm() MUSS aufgerufen werden, auch mit leerem Attribut --
        // Ablehnung muss den Submit weiterhin verhindern.
        expect(confirmSpy).toHaveBeenCalledTimes(1);
        expect(form.requestSubmit).not.toHaveBeenCalled();
        confirmSpy.mockRestore();
    });

    it("der programmatische Re-Submit loest den Guard nicht erneut aus (kein Endlosloop)", async () => {
        const form = makeForm();
        vi.spyOn(window, "confirm").mockReturnValue(true);
        guard.attachGuard(form);

        form.dispatchEvent(new Event("submit", { cancelable: true }));
        await flushMicrotasks();
        expect(form.requestSubmit).toHaveBeenCalledTimes(1);

        // Simuliert den echten Re-Submit-Pfad: bypass ist jetzt gesetzt,
        // ein zweites "submit" (z. B. durch form.requestSubmit() selbst)
        // darf den Zaehl-Check nicht nochmal durchlaufen.
        form.dispatchEvent(new Event("submit", { cancelable: true }));
        await flushMicrotasks();
        expect(form.requestSubmit).toHaveBeenCalledTimes(1);
    });
});

describe("Fail-Open bei fehlgeschlagenem Zaehl-Check", () => {
    it("sendet trotzdem ab, wenn countUnsyncedEvents ablehnt", async () => {
        window.offlineStore = {
            countUnsyncedEvents: async () => {
                throw new Error("boom");
            },
            countQueueByStatus: async () => ({ total: 0 }),
        };
        const form = makeForm();
        const confirmSpy = vi.spyOn(window, "confirm");
        guard.attachGuard(form);

        form.dispatchEvent(new Event("submit", { cancelable: true }));
        await flushMicrotasks();

        expect(confirmSpy).not.toHaveBeenCalled();
        expect(form.requestSubmit).toHaveBeenCalledTimes(1);
        confirmSpy.mockRestore();
        delete window.offlineStore;
    });
});
