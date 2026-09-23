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
        // Frischer ``body``: htmx-errors.js haengt seinen Listener dort an,
        // und jeder Re-Import wuerde einen weiteren dazulegen — der zweite
        // Test saehe dann zwei Purges/Reloads.
        document.body.remove();
        document.documentElement.appendChild(document.createElement("body"));
        run = vi.fn(async () => {});
        window.facilityArchivedPurge = { run: run };
        // ``location`` wird ersetzt, damit ``reload`` beobachtbar ist. Seit
        // Cross-Review NF1 ist der Reload ausdruecklich Pruefobjekt: er ist
        // der Punkt, an dem die Nutzerin die gesperrte Oberflaeche verlaesst,
        // und genau er fiel bei einem haengenden Purge aus.
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
        delete window.__logoutInFlight;
    });

    it("purgt bei markierter 403, zeigt KEINEN Toast und verlaesst die Oberflaeche", async () => {
        dispatchResponseError(fakeXhr({ status: 403, archived: true }));

        await vi.waitFor(() => expect(run).toHaveBeenCalledTimes(1));
        expect(document.getElementById("htmx-error-toast")).toBeNull();
        // Cross-Review „Reload nicht unter Test": der Redirect ist der Punkt,
        // an dem die Nutzerin die gesperrte Oberflaeche tatsaechlich verlaesst.
        await vi.waitFor(() => expect(window.location.reload).toHaveBeenCalledTimes(1));
    });

    it("verlaesst die Oberflaeche auch bei NIE erfuelltem Purge-Promise", async () => {
        // Cross-Review NF1 am Rueckfall-Pfad: ohne Deckel wartete der Handler
        // dauerhaft vor ``location.reload()``.
        vi.useFakeTimers();
        try {
            window.facilityArchivedPurge = { run: () => new Promise(() => {}) };

            dispatchResponseError(fakeXhr({ status: 403, archived: true }));
            await vi.advanceTimersByTimeAsync(3000);

            expect(window.location.reload).toHaveBeenCalledTimes(1);
        } finally {
            vi.useRealTimers();
        }
    });

    // Refs #2360: ``_handleArchivedFacility`` startete den Purge und rief den
    // Reload danach UNGEPRUEFT auf. Setzt ein Schnell-Verlassen die
    // Logout-Marke (``window.__logoutInFlight``, Vertrag aus sw-register.js)
    // erst waehrend des laufenden Purges, sah der Reload sie bisher nie.
    it("unterdrueckt den Reload, wenn die Logout-Marke erst waehrend des Purges gesetzt wird", async () => {
        run = vi.fn(async () => {
            // Das Schnell-Verlassen setzt die Marke MITTEN im asynchronen
            // Purge — vor dem Dispatch war sie noch nicht da.
            window.__logoutInFlight = Date.now();
        });
        window.facilityArchivedPurge = { run: run };

        dispatchResponseError(fakeXhr({ status: 403, archived: true }));

        await vi.waitFor(() => expect(run).toHaveBeenCalledTimes(1));
        // Kurz nachschwingen lassen, damit ``.then()`` nach dem Purge sicher
        // gelaufen ist — ein NICHT aufgerufener Reload hat keine eigene
        // Warte-Bedingung, auf die ``vi.waitFor`` warten koennte.
        await new Promise((resolve) => setTimeout(resolve, 20));
        expect(window.location.reload).not.toHaveBeenCalled();
    });

    it("Kontrollfall: verlaesst die Oberflaeche weiterhin, wenn waehrenddessen KEINE Marke gesetzt wird", async () => {
        dispatchResponseError(fakeXhr({ status: 403, archived: true }));

        await vi.waitFor(() => expect(run).toHaveBeenCalledTimes(1));
        await vi.waitFor(() => expect(window.location.reload).toHaveBeenCalledTimes(1));
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

    it("erreicht die Folgephasen auch bei NIE erfuelltem Wipe-Promise", async () => {
        // Cross-Review NF1: der Deckel galt nur fuer ``deleteDatabase``; auf
        // ``wipeOfflineState()`` wartete ``run()`` unbegrenzt. Ein haengender
        // erster Promise liess DB-Loeschung, SW-Unregister UND den Reload des
        // HTMX-Handlers ausfallen. Ein geworfener Fehler (Test darunter)
        // deckt diesen Fall NICHT ab — ein nie erfuelltes Promise wirft nie.
        vi.useFakeTimers();
        try {
            window.crypto_session = { wipeOfflineState: () => new Promise(() => {}) };
            await import("../../src/static/js/facility-archived-purge.js");

            let settled = false;
            const running = window.facilityArchivedPurge.run().then(() => {
                settled = true;
            });
            await vi.advanceTimersByTimeAsync(3000);
            await running;

            expect(settled).toBe(true);
            expect(deleted).toEqual(["anlaufstelle-offline", "anlaufstelle-crypto"]);
            expect(unregistered).toBe(1);
        } finally {
            vi.useRealTimers();
        }
    });

    it("haelt das Gesamtbudget ein, wenn MEHRERE Phasen haengen", async () => {
        // Drei Phasen mit je eigenem Deckel summierten sich sonst auf das
        // Dreifache. Das Budget gilt deshalb fuer den gesamten Lauf.
        vi.useFakeTimers();
        try {
            window.crypto_session = { wipeOfflineState: () => new Promise(() => {}) };
            Object.defineProperty(window.navigator, "serviceWorker", {
                configurable: true,
                value: { getRegistrations: () => new Promise(() => {}) },
            });
            await import("../../src/static/js/facility-archived-purge.js");

            let settled = false;
            const running = window.facilityArchivedPurge.run().then(() => {
                settled = true;
            });
            await vi.runAllTimersAsync();
            await running;

            expect(settled).toBe(true);
        } finally {
            vi.useRealTimers();
        }
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
