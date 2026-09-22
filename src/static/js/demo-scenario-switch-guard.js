/*
 * Warnt vor ungespeicherter Offline-Arbeit beim Demo-Start/-Wechsel (Refs
 * #2285, Fix-Runde 1, Ticket-Punkt 9: "Ungespeicherte Arbeit vorher
 * nachvollziehbar behandeln; weder still verlieren noch übertragen").
 *
 * Gleiche Zaehl-/Bestaetigungs-Mechanik wie auth-bootstrap.js
 * (unsyncedEntryCount/attachUnsyncedGuard, Refs #1415/#2287 — dort fuer den
 * Passwortwechsel) — hier repliziert, KEIN neuer Store: beide Zaehlfunktionen
 * kommen unveraendert aus window.offlineStore (countUnsyncedEvents/
 * countQueueByStatus). Eine gemeinsame Modul-Datei gibt es nicht (kein
 * Modulsystem/keine Exports zwischen den beiden IIFE-Skripten), deshalb die
 * kleine Aggregationsfunktion dupliziert statt importiert.
 *
 * FAIL-OPEN (Pflicht, wie beim Vorbild): schlaegt der Zaehl-Check fehl oder
 * ist window.offlineStore nicht initialisiert (z. B. anonymer Erstbesuch auf
 * /demo/ vor jedem Login — es gibt dann naturgemaess nichts zu verlieren),
 * geht der Start/Wechsel ungehindert durch. `confirm()` erscheint nur, wenn
 * der Zaehler nachweislich > 0 ist.
 *
 * Async/sync-Bruch (`confirm()` ist synchron, der Zaehl-Check async):
 * `preventDefault` + `stopImmediatePropagation` auf dem ersten Submit, dann
 * nach dem asynchronen Check ein programmatischer `form.requestSubmit()`.
 * Ein `bypass`-Flag pro Formular laesst den zweiten, programmatischen Submit
 * unangetastet durch.
 */
(function () {
    "use strict";

    // Generischer Rueckfalltext, falls data-i18n-demo-switch-confirm auf der
    // Seite fehlt oder leer ist: ohne ihn wuerde ein leeres Attribut den
    // Bestaetigungsdialog bei count > 0 stillschweigend uebergehen (stiller
    // statt sichtbarer Fail-Open). Deutsch als Default, wie die uebrigen
    // literalen Skriptausgaben dieses Verbunds.
    var FALLBACK_CONFIRM_TEXT = "{count} Änderungen sind noch nicht übertragen und gehen beim Wechsel verloren. Trotzdem wechseln?";

    function unsyncedEntryCount() {
        if (!window.offlineStore) return Promise.resolve(0);
        var events =
            typeof window.offlineStore.countUnsyncedEvents === "function"
                ? window.offlineStore.countUnsyncedEvents()
                : Promise.resolve(0);
        var queue =
            typeof window.offlineStore.countQueueByStatus === "function"
                ? window.offlineStore.countQueueByStatus()
                : Promise.resolve({ total: 0 });
        return Promise.all([events, queue]).then(function (results) {
            return (results[0] || 0) + ((results[1] && results[1].total) || 0);
        });
    }

    function attachGuard(form) {
        var bypass = false;
        form.addEventListener("submit", function (event) {
            if (bypass) return; // programmatischer Re-Submit nach confirm/Fail-Open
            event.preventDefault();
            event.stopImmediatePropagation();
            unsyncedEntryCount()
                .then(function (count) {
                    if (count > 0) {
                        var template = document.body.getAttribute("data-i18n-demo-switch-confirm") || FALLBACK_CONFIRM_TEXT;
                        var msg = template.replace("{count}", String(count));
                        if (!window.confirm(msg)) return; // Abbruch
                    }
                    bypass = true;
                    if (form.requestSubmit) form.requestSubmit();
                    else form.submit();
                })
                .catch(function () {
                    // FAIL-OPEN: Zaehl-Check fehlgeschlagen -> nicht blockieren.
                    bypass = true;
                    if (form.requestSubmit) form.requestSubmit();
                    else form.submit();
                });
        });
    }

    function _autostart() {
        var forms = document.querySelectorAll("[data-demo-scenario-form]");
        for (var i = 0; i < forms.length; i++) {
            attachGuard(forms[i]);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", _autostart);
    } else {
        _autostart();
    }

    // Test-/Debug-Oberflaeche (Muster wie window.offlineQueue._backoffFor):
    // vitest greift auf die reine Zaehl-/Attach-Logik zu, ohne DOM-Events
    // simulieren zu muessen.
    window.demoScenarioSwitchGuard = {
        unsyncedEntryCount: unsyncedEntryCount,
        attachGuard: attachGuard,
    };
})();
