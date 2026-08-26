// Refs #1994 — pagehide-Zweig der Slot-Entwaffnung aus offline-db.js.
//
// offline-db.js entwaffnet seinen Verbrauchs-Slot im Parser-Pfad an ZWEI
// Ereignissen: ``DOMContentLoaded`` (Normalfall, gepinnt in
// offline-store-split.test.mjs) und ``pagehide`` — der Abbruch-Fall, in dem
// der Parse nie fertig wird, weil die Seite waehrenddessen verlassen wird.
// Der pagehide-Zweig war bis #1994 komplett ungetestet: die Suiten liefen
// ueber den Makrotask-Fallback, in dem er gar nicht registriert wird. Ohne
// ihn bliebe der Slot samt ``_crypto`` (R8-Entschluesselungs-Orakel mit frei
// waehlbarem AAD) auf einem abgebrochenen Parse liegen — im bfcache lebt das
// window weiter.
//
// EIGENE Datei, nicht in offline-store-split.test.mjs: pagehide-Listener sind
// ``{ once: true }``, aber pro geladener offline-db.js-INSTANZ einer. In einer
// Suite, deren beforeAll den Verbund schon geladen hat, wuerde ein spaeteres
// ``dispatchEvent(new Event("pagehide"))`` auch den Listener der ALTEN Instanz
// ausloesen — der raeumt denselben window-Slot ab und der Test waere gruen,
// ohne die frisch geladene Registrierung je zu beruehren. Hier laedt beforeAll
// deshalb NUR die Voraussetzungen (crypto/Dexie), und der erste, allein
// aussagekraeftige Test bekommt die einzige offline-db.js-Instanz der Datei.
import Dexie from "dexie";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { loadOfflineStoreModulesDuringParse, openParseWindow } from "./_offline_store_modules.mjs";

beforeAll(async () => {
    // offline-store.js prueft `typeof Dexie === "undefined"` beim Laden.
    window.Dexie = Dexie;
    globalThis.Dexie = Dexie;
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.deriveSessionKey("correct horse battery", "c2FsdHNhbHRzYWx0");
});

async function encryptedRoundtrip(store, url) {
    const id = await store.putEncrypted("queue", {
        url: url,
        createdAt: Date.now(),
        retryAfter: 0,
        localStatus: null,
        data: { method: "POST", body: "pagehide=1", headers: {} },
    });
    const rows = await store.listDecrypted("queue", (r) => r.url === url);
    expect(rows).toHaveLength(1);
    expect(rows[0].data).toEqual({ method: "POST", body: "pagehide=1", headers: {} });
    await store.deleteRow("queue", id);
}

describe("#1994 — pagehide entwaffnet den Verbrauchs-Slot", () => {
    it("raeumt den noch NICHT erschoepften Slot ab, wenn der Parse abbricht", async () => {
        // Nur offline-db.js: das Abhol-Budget (3 Schwestern) bleibt offen, der
        // Slot steht also noch. Erst dadurch misst der pagehide-Dispatch unten
        // wirklich den Listener — nach einem Voll-Ladevorgang haette bereits
        // take() selbst abgeschlossen und der Test waere aussagelos gruen.
        const prevStore = window.offlineStore;
        delete window.offlineStore;
        delete window.__offlineStoreInternals;
        const endParseWindow = openParseWindow();
        try {
            await import("../../src/static/js/offline-db.js");
        } finally {
            endParseWindow();
        }

        try {
            expect(window.__offlineStoreInternals).toBeDefined();
            // Eine Abholung von dreien — der Slot MUSS danach noch stehen.
            const handed = window.__offlineStoreInternals.take();
            expect(handed).not.toBeNull();
            expect(typeof handed.putEncrypted).toBe("function");
            expect(window.__offlineStoreInternals).toBeDefined();

            window.dispatchEvent(new Event("pagehide"));

            expect(window.__offlineStoreInternals).toBeUndefined();
            expect("__offlineStoreInternals" in window).toBe(false);

            // Idempotenz von _disarmInternals: ein zweites pagehide und das
            // danach noch faellige DOMContentLoaded duerfen weder werfen noch
            // den Slot wiederbeleben. (Der pagehide-Listener ist
            // ``{ once: true }``, der DCL-Listener ist derselbe Callback —
            // die zweite Ausfuehrung ist real und laeuft hier durch.)
            window.dispatchEvent(new Event("pagehide"));
            document.dispatchEvent(new Event("DOMContentLoaded"));
            expect(window.__offlineStoreInternals).toBeUndefined();
            // Der bereits abgeholte Handshake bleibt gueltig — die Schwestern
            // halten Closure-Referenzen, kein Spaet-Zugriff auf den Slot.
            expect(typeof handed.putEncrypted).toBe("function");
        } finally {
            window.offlineStore = prevStore;
        }
    });

    it("laesst die Fassade funktionsfaehig, wenn pagehide VOR DOMContentLoaded feuert", async () => {
        // Voller Verbund im Parser-Fenster, dann pagehide STATT/VOR
        // DOMContentLoaded. Der Slot ist hier schon durch die Budget-
        // Erschoepfung weg; gepinnt wird, was pagehide zusaetzlich ausloest:
        // _disarmInternals laeuft ein weiteres Mal, und der Verbund muss das
        // ueberleben (er arbeitet aus Closures, nicht aus dem Slot).
        vi.resetModules();
        delete window.offlineStore;
        delete window.__offlineStoreInternals;
        const endParseWindow = await loadOfflineStoreModulesDuringParse();
        endParseWindow();

        expect(window.__offlineStoreInternals).toBeUndefined();

        window.dispatchEvent(new Event("pagehide"));

        const store = window.offlineStore;
        expect(typeof store.putEncrypted).toBe("function");
        expect(typeof store.saveClientBundle).toBe("function");
        await encryptedRoundtrip(store, "/pagehide-vor-dcl/");

        // Und danach das regulaere DOMContentLoaded — dritter _disarmInternals-
        // Aufruf, weiterhin folgenlos.
        document.dispatchEvent(new Event("DOMContentLoaded"));
        expect(window.__offlineStoreInternals).toBeUndefined();
        await encryptedRoundtrip(store, "/pagehide-dann-dcl/");
    });
});
