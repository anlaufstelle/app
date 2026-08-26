// Refs #1838: EINE Stelle fuer die Modul-Liste des offline-store-Verbunds.
// Die Harnesse, die den Store echt laden, importieren hier — in exakt der
// Script-Tag-Reihenfolge von base.html/offline_base.html. Waechst der
// Verbund, waechst NUR diese Liste.
//
// url-patterns.js und crypto.js bleiben bewusst Sache der Harnesse (manche
// leiten zwischen crypto-Import und Store-Import erst den Session-Key ab).
// crypto.js MUSS vor diesem Helper geladen sein (sein Idle-Check-Interval
// soll REAL bleiben).
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
// per runAllTimers(). Damit war der PRODUKTIVE Zweig in ~30 Suiten nie aktiv.
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
 */
export async function loadOfflineStoreModulesDuringParse() {
    const endParseWindow = openParseWindow();
    try {
        for (const path of MODULES) {
            await import(/* @vite-ignore */ path);
        }
    } catch (err) {
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
