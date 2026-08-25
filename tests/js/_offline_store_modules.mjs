// Refs #1838: EINE Stelle fuer die Modul-Liste des offline-store-Verbunds.
// Die Harnesse, die den Store echt laden, importieren hier — in exakt der
// Script-Tag-Reihenfolge von base.html/offline_base.html. Waechst der
// Verbund, waechst NUR diese Liste.
//
// url-patterns.js und crypto.js bleiben bewusst Sache der Harnesse (manche
// leiten zwischen crypto-Import und Store-Import erst den Session-Key ab).
//
// Fake-Timer waehrend des Ladens (Refs #1838, Slot-Selbstentwaffnung):
// offline-db.js entwaffnet den internen Kanal budget-unabhaengig per
// setTimeout(0) — im Browser laeuft dieser Makrotask garantiert NACH allen
// synchronen Verbund-Script-Tags. Kalte dynamic imports im Test-Runner haben
// dagegen echte Event-Loop-Luecken, in denen der Timer ZU FRUEH feuern und
// den Slot vor den Schwester-Abholungen loeschen wuerde. Die Fake-Timer
// halten ihn an; runAllTimers() feuert ihn danach — exakt die
// Browser-Reihenfolge. crypto.js MUSS vor diesem Helper geladen sein (sein
// Idle-Check-Interval soll REAL bleiben).
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
