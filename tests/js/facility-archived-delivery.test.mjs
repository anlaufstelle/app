// Refs #2067 (Cross-Review Finding 1, Rest aus Fix-Runde 1): Zustellung des
// Archiv-Purges auf den beiden Wegen NEBEN der Bundle-Revalidierung.
//
// Fix-Runde 1 markierte die Sperr-403 und liess `revalidateCachedClient` /
// `revalidateCachedFacility` darauf purgen. Navigationen und HTMX-Antworten
// reicht der Service Worker aber direkt vom Netz weiter — dort wertete
// niemand den Marker aus, und der Entzug haette allein an `Clear-Site-Data`
// gehangen, das Safari laut eigener security-notes.md nur unvollstaendig
// umsetzt.
//
// Diese Suite pinnt beide neuen Wege UND in beiden den #1354-Waechter: ein
// nacktes 403 (CSRF-/Rate-Limit-/Proxy-Rauschen) darf NICHTS purgen.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** Minimaler XHR-Doppelgaenger, wie htmx ihn in `event.detail.xhr` liefert. */
function fakeXhr({ status, archived, body = "" }) {
    return {
        status: status,
        responseText: body,
        getResponseHeader(name) {
            return name === "X-Facility-Archived" && archived ? "1" : null;
        },
    };
}

function dispatchResponseError(xhr) {
    const event = new Event("htmx:responseError", { bubbles: true });
    event.detail = { xhr: xhr };
    document.body.dispatchEvent(event);
}

describe("HTMX-Zustellweg", () => {
    let run;

    beforeEach(async () => {
        vi.resetModules();
        document.body.innerHTML = "";
        run = vi.fn(async () => {});
        window.facilityArchivedPurge = { run: run };
        // ``reload`` ist in jsdom nicht schreibbar — die Navigation ist hier
        // nicht das Pruefobjekt. Getestet wird, DASS gepurgt und KEIN Toast
        // gezeigt wird; der Reload ist der Einzeiler dahinter.
        try {
            Object.defineProperty(window, "location", {
                configurable: true,
                value: { ...window.location, reload: vi.fn() },
            });
        } catch (_e) {
            // egal — die Assertions unten haengen nicht daran.
        }
        await import("../../src/static/js/htmx-errors.js");
    });

    afterEach(() => {
        delete window.facilityArchivedPurge;
    });

    it("purgt bei markierter 403 und zeigt KEINEN Toast", async () => {
        dispatchResponseError(fakeXhr({ status: 403, archived: true }));

        await vi.waitFor(() => expect(run).toHaveBeenCalledTimes(1));
        expect(document.getElementById("htmx-error-toast")).toBeNull();
    });

    it("purgt NICHT bei einer 403 ohne Marker und zeigt den Toast (Refs #1354)", async () => {
        dispatchResponseError(fakeXhr({ status: 403, archived: false }));

        await vi.waitFor(() => expect(document.getElementById("htmx-error-toast")).not.toBeNull());
        expect(run).not.toHaveBeenCalled();
    });

    it("purgt NICHT bei anderen Fehlerstatus mit gesetztem Marker", async () => {
        // Defense-in-Depth: der Marker allein genuegt nicht, es braucht 403.
        dispatchResponseError(fakeXhr({ status: 500, archived: true }));

        await vi.waitFor(() => expect(document.getElementById("htmx-error-toast")).not.toBeNull());
        expect(run).not.toHaveBeenCalled();
    });

    it("laesst den 429-Rueckfalltext unveraendert", async () => {
        dispatchResponseError(fakeXhr({ status: 429, archived: false }));

        await vi.waitFor(() => {
            const toast = document.getElementById("htmx-error-toast");
            expect(toast).not.toBeNull();
            expect(toast.textContent).toContain("Zu viele Anfragen");
        });
        expect(run).not.toHaveBeenCalled();
    });
});

describe("Sperrseiten-Zustellweg (eigenstaendiges Modul)", () => {
    let deleted;
    let unregistered;

    beforeEach(() => {
        vi.resetModules();
        document.body.innerHTML = "";
        deleted = [];
        unregistered = 0;

        // ``deleteDatabase`` ist der tragende Schritt: die Sperrseite laedt
        // weder Dexie noch crypto.js, der Wipe muss ohne den Verbund wirken.
        vi.stubGlobal("indexedDB", {
            deleteDatabase(name) {
                deleted.push(name);
                const request = {};
                queueMicrotask(() => request.onsuccess && request.onsuccess());
                return request;
            },
        });
        Object.defineProperty(window.navigator, "serviceWorker", {
            configurable: true,
            value: {
                getRegistrations: async () => [
                    {
                        unregister: async () => {
                            unregistered += 1;
                            return true;
                        },
                    },
                ],
            },
        });
        delete window.crypto_session;
        delete window.offlineStore;
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        delete window.facilityArchivedPurge;
    });

    it("startet von selbst, sobald der DOM-Marker da ist", async () => {
        document.body.innerHTML = '<div data-facility-archived-purge></div>';

        await import("../../src/static/js/facility-archived-purge.js");

        await vi.waitFor(() => expect(deleted).toEqual(["anlaufstelle-offline", "anlaufstelle-crypto"]));
        await vi.waitFor(() => expect(unregistered).toBe(1));
    });

    it("bleibt ohne Marker inert", async () => {
        await import("../../src/static/js/facility-archived-purge.js");

        // Kurz laufen lassen — ein Autostart wuerde hier sichtbar.
        await new Promise((resolve) => setTimeout(resolve, 10));
        expect(deleted).toEqual([]);
        expect(unregistered).toBe(0);
        expect(typeof window.facilityArchivedPurge.run).toBe("function");
    });

    it("nutzt zuerst den gemeinsamen Wipe-Pfad, wenn der Verbund geladen ist", async () => {
        // Nur er verwirft zusaetzlich den CryptoKey im Speicher der Seite.
        const wipeOfflineState = vi.fn(async () => {});
        window.crypto_session = { wipeOfflineState };

        await import("../../src/static/js/facility-archived-purge.js");
        await window.facilityArchivedPurge.run();

        expect(wipeOfflineState).toHaveBeenCalledTimes(1);
        expect(deleted).toEqual(["anlaufstelle-offline", "anlaufstelle-crypto"]);
    });

    it("laeuft auch durch, wenn der gemeinsame Wipe-Pfad wirft", async () => {
        window.crypto_session = {
            wipeOfflineState: async () => {
                throw new Error("IndexedDB blockiert");
            },
        };

        await import("../../src/static/js/facility-archived-purge.js");
        await window.facilityArchivedPurge.run();

        // Der harte Weg muss den Fehler ueberleben — sonst bliebe der
        // Bestand genau dann liegen, wenn der weiche Pfad klemmt.
        expect(deleted).toEqual(["anlaufstelle-offline", "anlaufstelle-crypto"]);
    });
});
