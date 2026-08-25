// Refs #1838: EINE Stelle fuer die Modul-Liste des offline-store-Verbunds.
// Die Harnesse, die den Store echt laden, importieren hier — in exakt der
// Script-Tag-Reihenfolge von base.html/offline_base.html. Waechst der
// Verbund (Split-Commits), waechst NUR diese Liste.
//
// url-patterns.js und crypto.js bleiben bewusst Sache der Harnesse (manche
// leiten zwischen crypto-Import und Store-Import erst den Session-Key ab).
export async function importOfflineStoreModules() {
    await import("../../src/static/js/offline-db.js");
    await import("../../src/static/js/offline-store.js");
}
