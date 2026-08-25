// Refs #1838: EINE Stelle fuer die Modul-Liste des offline-store-Verbunds.
// Die Harnesse, die den Store echt laden, importieren hier — in exakt der
// Script-Tag-Reihenfolge von base.html/offline_base.html. Waechst der
// Verbund, waechst NUR diese Liste.
//
// url-patterns.js und crypto.js bleiben bewusst Sache der Harnesse (manche
// leiten zwischen crypto-Import und Store-Import erst den Session-Key ab).
//
// Fake-Timer waehrend des Ladens (Refs #1838, Slot-Selbstentwaffnung):
// Im Browser entwaffnet offline-db.js den internen Kanal an
// DOMContentLoaded (feuert erst nach allen parser-blockierenden
// Verbund-Tags); ein setTimeout(0) traegt diese Garantie NICHT — der Timer
// kann in der Fetch-Luecke zwischen zwei Script-Tags feuern (2x per PoC
// belegt, s. #1838). In jsdom ist document.readyState beim Testlauf bereits
// "complete", darum nimmt offline-db.js hier den Makrotask-Fallback; die
// Fake-Timer halten ihn an, runAllTimers() feuert ihn nach den Imports.
// Den echten DCL-Pfad pinnt offline-store-split.test.mjs isoliert
// (readyState "loading" erzwungen); end-to-end deckt ihn die E2E-Suite ab.
// crypto.js MUSS vor diesem Helper geladen sein (sein Idle-Check-Interval
// soll REAL bleiben).
import { vi } from "vitest";

export async function importOfflineStoreModules() {
    vi.useFakeTimers();
    try {
        await import("../../src/static/js/offline-db.js");
        await import("../../src/static/js/offline-bundles.js");
        await import("../../src/static/js/offline-revocations.js");
        await import("../../src/static/js/offline-store.js");
        vi.runAllTimers();
    } finally {
        vi.useRealTimers();
    }
}
