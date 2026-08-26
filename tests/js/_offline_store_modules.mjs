// Refs #1838: EINE Stelle fuer die Modul-Liste des offline-store-Verbunds.
// Die Harnesse, die den Store echt laden, importieren hier — in exakt der
// Script-Tag-Reihenfolge von base.html/offline_base.html. Waechst der
// Verbund, waechst NUR diese Liste.
//
// url-patterns.js und crypto.js bleiben bewusst Sache der Harnesse (manche
// leiten zwischen crypto-Import und Store-Import erst den Session-Key ab).
// crypto.js MUSS vor diesem Helper geladen sein — dieselbe Reihenfolge wie die
// Script-Tags in base.html: offline-db.js bezieht seine Krypto-Primitive per
// Einmal-Handshake aus ``window.crypto_session._link()`` (R8, Refs #1476);
// ohne die Oberflaeche wirft sein ``_crypto()`` "CryptoSessionNotLoaded".
//
// Refs #1994 — echter DOMContentLoaded-Pfad statt Makrotask-Fallback:
// offline-db.js entwaffnet seinen internen Kanal im Browser an
// DOMContentLoaded (feuert erst nach allen parser-blockierenden Verbund-Tags)
// und zusaetzlich an pagehide; ein setTimeout(0) traegt diese Garantie NICHT
// — der Timer kann in der Fetch-Luecke zwischen zwei Script-Tags feuern (2x
// per PoC belegt, s. #1838). Nur wenn ``document.readyState`` beim Laden
// NICHT "loading" ist (Test-Harness via dynamic import), faellt offline-db.js
// auf den Makrotask zurueck. Genau dieser Fallback lief bis #1994 in JEDER
// Suite dieses Helfers: jsdom meldet beim Testlauf bereits "complete", der
// Helper hielt den Timer mit Fake-Timern an und feuerte ihn nach den Imports
// per runAllTimers(). Damit war der PRODUKTIVE Zweig in den 5 Testdateien
// dieses Helfers (67 it()-Faelle) nie aktiv.
//
// Der Helper simuliert deshalb jetzt das Parser-Fenster: readyState wird auf
// "loading" gezwungen, der Verbund geladen, das Fenster geschlossen und
// DOMContentLoaded dispatcht. Fake-Timer braucht es dafuer nicht mehr — es
// gibt keinen Disarm-Timer mehr anzuhalten, und echte Timer halten die
// Umgebung naeher am Browser (der Selbsttest dazu steht in
// offline-store-split.test.mjs, "#1994 — der Harness durchlaeuft den echten
// DOMContentLoaded-Pfad"). Den Makrotask-Fallback pinnt weiterhin
// offline-store-split.test.mjs ("Slot-Selbstentwaffnung ohne offline-store.js")
// ueber seine eigene Lade-Sequenz.

const MODULES = [
    "../../src/static/js/offline-db.js",
    "../../src/static/js/offline-bundles.js",
    "../../src/static/js/offline-revocations.js",
    "../../src/static/js/offline-store.js",
];

/**
 * Erzwingt ``document.readyState`` und liefert die exakte Ruecknahme.
 *
 * jsdom traegt ``readyState`` als Getter auf ``Document.prototype``; ein
 * ``Object.defineProperty`` auf dem Dokument legt eine eigene Property
 * darueber. Die Ruecknahme entfernt sie wieder (bzw. stellt eine zuvor
 * vorhandene eigene Property her), damit spaeter geladene Module — autosave.js
 * und offline-tally.js verzweigen selbst auf readyState — wieder den echten
 * Wert sehen.
 */
function _forceReadyState(value) {
    const ownDescriptor = Object.getOwnPropertyDescriptor(document, "readyState");
    Object.defineProperty(document, "readyState", { configurable: true, value: value });
    return function restoreReadyState() {
        delete document.readyState;
        if (ownDescriptor) Object.defineProperty(document, "readyState", ownDescriptor);
    };
}

/**
 * Zaehlt Registrierungen EINES Ereignistyps am ``target`` und stellt
 * ``addEventListener`` beim (idempotenten) Stoppen exakt wieder her.
 *
 * Ein aeusserer Spy auf ``addEventListener`` — der Selbsttest in
 * offline-store-split.test.mjs legt einen an — bleibt intakt: der Wrapper
 * hier ruft ihn durch und wird LIFO vor ihm zurueckgenommen.
 *
 * Der Wrapper wird per ``Object.defineProperty`` gesetzt, NICHT per Zuweisung:
 * vi.spyOn legt auf ``window`` eine ACCESSOR-Property an, eine Zuweisung liefe
 * also in deren Setter und installierte den Wrapper nie (empirisch belegt —
 * der Zaehler blieb still auf 0).
 */
function _trackRegistrations(target, type) {
    const ownDescriptor = Object.getOwnPropertyDescriptor(target, "addEventListener");
    const original = target.addEventListener;
    let count = 0;
    let stopped = false;
    Object.defineProperty(target, "addEventListener", {
        configurable: true,
        writable: true,
        value: function (eventType, ...rest) {
            if (eventType === type) count += 1;
            return original.call(this, eventType, ...rest);
        },
    });
    return function stop() {
        if (!stopped) {
            stopped = true;
            delete target.addEventListener;
            if (ownDescriptor) Object.defineProperty(target, "addEventListener", ownDescriptor);
        }
        return count;
    };
}

/**
 * Oeffnet ein Parser-Fenster (readyState "loading") und liefert dessen
 * Schliesser.
 *
 * Fuer Suiten, die eine EIGENE Lade-Sequenz brauchen (etwa nur offline-db.js,
 * um den Verbrauchs-Slot vor der Budget-Erschoepfung zu beobachten), aber
 * denselben Browser-Zweig treffen sollen wie ``importOfflineStoreModules``.
 */
export function openParseWindow() {
    return _forceReadyState("loading");
}

/**
 * Laedt den Verbund IM offenen Parser-Fenster (readyState "loading").
 *
 * Gibt die Funktion zurueck, die das Fenster schliesst (readyState-Ruecknahme).
 * Der Aufrufer entscheidet danach selbst, WIE der Parse endet — im Normalfall
 * per DOMContentLoaded (siehe ``importOfflineStoreModules``), fuer den
 * Abbruch-Zweig per pagehide. Wirft ein Import, wird das Fenster geschlossen
 * und der Fehler weitergereicht.
 *
 * Der Helper prueft selbst, dass offline-db.js den Parser-Zweig GENOMMEN hat
 * (je genau eine DOMContentLoaded- und pagehide-Registrierung) und schlaegt
 * sonst laut fehl. Ohne diesen Wachhund koennte ein kuenftiger Aufrufer, der
 * offline-db.js vorher importiert, still am Modul-Cache haengenbleiben: die
 * IIFE laeuft kein zweites Mal, es wird GAR NICHTS registriert — und kein Test
 * wuerde es merken, weil der Slot aus dem ersten Lauf laengst abgeraeumt ist.
 */
export async function loadOfflineStoreModulesDuringParse() {
    const endParseWindow = openParseWindow();
    const stopDclCount = _trackRegistrations(document, "DOMContentLoaded");
    const stopPagehideCount = _trackRegistrations(window, "pagehide");
    try {
        for (const path of MODULES) {
            await import(/* @vite-ignore */ path);
        }
        const dcl = stopDclCount();
        const pagehide = stopPagehideCount();
        if (dcl !== 1 || pagehide !== 1) {
            throw new Error(
                `offline-db.js hat den Parser-Zweig nicht genommen (DOMContentLoaded: ${dcl}, ` +
                    `pagehide: ${pagehide}, erwartet je 1). Haeufigste Ursache: der Verbund wurde ` +
                    `vor diesem Helper importiert — der Modul-Cache fuehrt die IIFE dann nicht ` +
                    `erneut aus. vi.resetModules() vor dem Aufruf.`
            );
        }
    } catch (err) {
        stopDclCount();
        stopPagehideCount();
        endParseWindow();
        throw err;
    }
    return endParseWindow;
}

/**
 * Der Normalfall: Verbund im Parser-Fenster laden, Fenster schliessen,
 * DOMContentLoaded dispatchen — also exakt der Browser-Pfad.
 *
 * Die Reihenfolge ist Absicht: im Browser ist ``readyState`` beim Feuern von
 * DOMContentLoaded bereits "interactive", also nicht mehr "loading". Ein
 * Listener, der darauf verzweigt, sieht hier denselben Zustand.
 */
export async function importOfflineStoreModules() {
    const endParseWindow = await loadOfflineStoreModulesDuringParse();
    endParseWindow();
    document.dispatchEvent(new Event("DOMContentLoaded"));
}
