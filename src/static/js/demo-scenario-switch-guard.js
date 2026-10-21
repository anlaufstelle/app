/*
 * Warn before discarding pending offline work on demo start/switch
 * (Refs #2285, #2602).
 * Reuse the offlineStore counters; missing or failed counts allow the start.
 * Only a positive count requires confirmation.
 *
 * Resume submission in a new task: a microtask can still run while the native
 * form algorithm is firing submission events and rejects requestSubmit().
 * Keep requestSubmit() so validation, the submitter and other listeners survive.
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
        var pending = false;
        form.addEventListener("submit", function (event) {
            if (bypass) return; // Allow the deferred resubmission through.
            event.preventDefault();
            event.stopImmediatePropagation();
            if (pending) return;
            pending = true;
            var submitter = event.submitter;
            Promise.resolve()
                .then(function () {
                    // Convert synchronous counter exceptions to rejections too.
                    return unsyncedEntryCount();
                })
                .catch(function () {
                    return 0; // Fail open if counting is unavailable.
                })
                .then(function (count) {
                    if (count > 0) {
                        var template = document.body.getAttribute("data-i18n-demo-switch-confirm") || FALLBACK_CONFIRM_TEXT;
                        var msg = template.replace("{count}", String(count));
                        if (!window.confirm(msg)) {
                            pending = false;
                            return;
                        }
                    }
                    window.setTimeout(function () {
                        bypass = true;
                        pending = false;
                        if (form.requestSubmit) {
                            if (submitter) form.requestSubmit(submitter);
                            else form.requestSubmit();
                        } else form.submit();
                    }, 0);
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
