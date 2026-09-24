// Refs #2060: Ein Rollback-Deploy oeffnet eine NEUERE IndexedDB mit aelterem
// Code. IndexedDB antwortet darauf mit einem ``VersionError`` — und den hat
// bisher niemand behandelt: Jede Store-Operation rejectete, die Offline-Daten
// lagen unerreichbar auf dem Geraet, und die Nutzerin sah je nach Aufrufer
// entweder gar nichts oder eine kryptische Meldung. Der Zustand ist real
// heilbar (Update ziehen), nur sagte das niemand.
//
// Der Fix haengt am Oeffnen selbst: EIN Ort, an dem der Fall entsteht, statt
// acht Aufrufer, die ihn je einzeln erraten muessten. Getestet gegen einen
// Dexie-Doppelgaenger, dessen ``open()`` genau diesen Fehler wirft — mit
// fake-indexeddb liesse sich der Zustand nicht herstellen.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const TOAST_ID = "offline-db-update-toast";

function fakeDexieClass(openResult) {
    const versionChain = { stores: () => versionChain, upgrade: () => versionChain };
    return class FakeDexie {
        constructor(name) {
            this.name = name;
        }
        version() {
            return versionChain;
        }
        open() {
            return openResult();
        }
        on() {}
    };
}

function versionError() {
    const error = new Error("Not the expected version.");
    error.name = "VersionError";
    return error;
}

async function loadOfflineDb(openResult) {
    vi.resetModules();
    delete window.offlineStore;
    delete window.__offlineStoreInternals;
    window.Dexie = fakeDexieClass(openResult);
    globalThis.Dexie = window.Dexie;
    window.crypto_session = { _link: () => ({ encryptPayload: async (d) => d, decryptPayload: async (d) => d }) };
    await import("../../src/static/js/offline-db.js");
    // Die open()-Rejection wird als Microtask zugestellt.
    await Promise.resolve();
    await Promise.resolve();
}

//
// Refs #2060 (Fix-Runde 1): Fuenf Templates laden ``offline-db.js``, aber nur
// ``base.html`` liefert ein ``data-i18n-db-update-required``. Der Toast trug
// die Meldung deshalb ausgerechnet dort nicht, wo sie am noetigsten ist —
// Anmeldung, Passwortwechsel, terminaler Offline-Fallback und die precachten
// In-Place-Shells. Er hat jetzt einen deutschen Rueckfall (dokumentierte
// Ausnahme nach dem Muster htmx-errors.js); das Template-Label gewinnt, wo es
// existiert.
const FALLBACK_TEXT = "Die lokal gespeicherten Daten stammen aus einer neueren Programmversion.";

/** Die fuenf Layouts, die ``offline-db.js`` laden — mit ihrem echten Dataset-Stand. */
const LAYOUTS = [
    { template: "base.html", label: "The locally stored data comes from a newer program version." },
    // Refs #2060 (Nachrunde): Anmeldung und Passwortwechsel liefern das Label
    // jetzt selbst — sie sind nicht precached, brauchten also keinen
    // CACHE_NAME-Bump.
    { template: "auth/login.html", label: "The locally stored data comes from a newer program version." },
    { template: "auth/password_change.html", label: "The locally stored data comes from a newer program version." },
    // Beide precached (Shell-Hash-Guard, Refs #1575/#1845) — sie bekommen das
    // Attribut erst mit dem naechsten CACHE_NAME-Bump und leben bis dahin vom
    // deutschen Rueckfall.
    { template: "offline_base.html", label: null },
    { template: "offline.html", label: null },
];

beforeEach(() => {
    document.body.innerHTML = "";
    document.body.dataset.i18nDbUpdateRequired = "App update required.";
    document.body.dataset.i18nSwDismiss = "Close";
});

afterEach(() => {
    delete window.__offlineStoreInternals;
});

describe("offline-db version mismatch", () => {
    it("tells the user that an app update is required", async () => {
        await loadOfflineDb(() => Promise.reject(versionError()));

        const toast = document.getElementById(TOAST_ID);
        expect(toast).not.toBeNull();
        expect(toast.textContent).toContain("App update required.");
    });

    it("prefers the translated template label over the fallback", async () => {
        document.body.dataset.i18nDbUpdateRequired = "Mise à jour requise.";

        await loadOfflineDb(() => Promise.reject(versionError()));

        const toast = document.getElementById(TOAST_ID);
        expect(toast.textContent).toContain("Mise à jour requise.");
        expect(toast.textContent).not.toContain(FALLBACK_TEXT);
    });

    it("still speaks up where no template supplies a label", async () => {
        // Refs #2060 (Fix-Runde 1): vorher brach der Toast hier ab und es
        // blieb nur console.error — also genau der Zustand vor dem Fix.
        delete document.body.dataset.i18nDbUpdateRequired;
        delete document.body.dataset.i18nSwDismiss;

        await loadOfflineDb(() => Promise.reject(versionError()));

        const toast = document.getElementById(TOAST_ID);
        expect(toast).not.toBeNull();
        expect(toast.textContent).toContain(FALLBACK_TEXT);
        expect(toast.querySelector("button").textContent).toBe("Schließen");
    });

    it.each(LAYOUTS)("reaches the user on $template", async ({ label }) => {
        // Jedes Layout, das offline-db.js laedt, muss eine LESBARE Meldung
        // bekommen — mit Label uebersetzt, ohne Label auf Deutsch. Leer ist
        // in keinem Fall zulaessig.
        if (label === null) {
            delete document.body.dataset.i18nDbUpdateRequired;
        } else {
            document.body.dataset.i18nDbUpdateRequired = label;
        }

        await loadOfflineDb(() => Promise.reject(versionError()));

        const toast = document.getElementById(TOAST_ID);
        expect(toast).not.toBeNull();
        expect(toast.textContent.trim().length).toBeGreaterThan(20);
        if (label !== null) expect(toast.textContent).toContain(label);
    });

    it("announces itself — the app is unusable offline until the update lands", async () => {
        await loadOfflineDb(() => Promise.reject(versionError()));

        expect(document.getElementById(TOAST_ID).getAttribute("role")).toBe("alert");
    });

    it("stays on screen without the Tailwind classes", async () => {
        // Refs #2387 (Muster #1386): die Klassen stehen nur hier im
        // JavaScript; fehlen sie in der ausgelieferten CSS, laege der Toast
        // unter dem Seitenende. Die sichtbarkeitskritischen Eigenschaften
        // stehen deshalb zusaetzlich inline am Element.
        await loadOfflineDb(() => Promise.reject(versionError()));

        const toast = document.getElementById(TOAST_ID);
        expect(toast.style.position).toBe("fixed");
        expect(toast.style.bottom).toBe("1rem");
        expect(toast.style.right).toBe("1rem");
        expect(toast.style.zIndex).toBe("50");
        expect(toast.style.backgroundColor).toBe("rgb(220, 38, 38)");
        expect(toast.style.color).toBe("rgb(255, 255, 255)");
        expect(toast.className).toContain("fixed bottom-4 right-4 z-50");
    });

    it("is dismissible", async () => {
        await loadOfflineDb(() => Promise.reject(versionError()));

        document.getElementById(TOAST_ID).querySelector("button").click();

        expect(document.getElementById(TOAST_ID)).toBeNull();
    });

    it("fires an event so other modules can react", async () => {
        const seen = vi.fn();
        window.addEventListener("offline-db-update-required", seen);

        await loadOfflineDb(() => Promise.reject(versionError()));

        expect(seen).toHaveBeenCalledTimes(1);
        window.removeEventListener("offline-db-update-required", seen);
    });

    it("prefers its own dismiss key over the service-worker one", async () => {
        // Layouts ohne Worker-Toast (auth/password_change.html) tragen einen
        // eigenen Schluessel — der Attributname des Workers waere dort falsch.
        document.body.dataset.i18nDbUpdateDismiss = "Dismiss";

        await loadOfflineDb(() => Promise.reject(versionError()));

        expect(document.getElementById(TOAST_ID).querySelector("button").textContent).toBe("Dismiss");
        delete document.body.dataset.i18nDbUpdateDismiss;
    });

    it("falls back to the service-worker dismiss key where that is what exists", async () => {
        delete document.body.dataset.i18nDbUpdateDismiss;

        await loadOfflineDb(() => Promise.reject(versionError()));

        expect(document.getElementById(TOAST_ID).querySelector("button").textContent).toBe("Close");
    });

    it("stays silent for every other open failure", async () => {
        await loadOfflineDb(() => Promise.reject(new Error("something else")));

        expect(document.getElementById(TOAST_ID)).toBeNull();
    });

    it("stays silent when the database opens normally", async () => {
        await loadOfflineDb(() => Promise.resolve());

        expect(document.getElementById(TOAST_ID)).toBeNull();
    });

    it("does not leave the open rejection unhandled", async () => {
        const unhandled = vi.fn();
        process.on("unhandledRejection", unhandled);

        await loadOfflineDb(() => Promise.reject(versionError()));
        await new Promise((resolve) => setTimeout(resolve, 10));

        process.off("unhandledRejection", unhandled);
        expect(unhandled).not.toHaveBeenCalled();
    });
});
