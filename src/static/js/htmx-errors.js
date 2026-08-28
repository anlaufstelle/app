/*
 * Globaler HTMX-Fehler-Handler (Refs #1016, Workstream C — C2).
 *
 * HTMX swappt bei 4xx/5xx-Antworten standardmäßig NICHT. Ohne Handler bleibt
 * eine fehlgeschlagene hx-Aktion für den Nutzer komplett unsichtbar — inkl.
 * der Klartext-400er aus retention.py / workitem_bulk.py ("Begründung ist
 * erforderlich." o.ä.), die heute still verschluckt werden. Dieser Handler
 * zeigt einen diskreten Fehler-Toast unten rechts.
 *
 * Reine DOM-Manipulation, CSP-konform (kein Inline-Script, kein eval) — spiegelt
 * das Toast-Muster aus sw-register.js (_showUpdatePrompt). Single-Toast: ein
 * bereits sichtbarer Toast wird wiederverwendet statt gestapelt.
 */
(function () {
    "use strict";

    var TOAST_ID = "htmx-error-toast";
    var GENERIC = "Die Aktion konnte nicht ausgeführt werden. Bitte erneut versuchen.";
    // Refs #1873: Rückfall-Text für 429 ohne verwertbaren Body. Der Regelfall
    // ist der übersetzte Klartext aus handler403 (core/views/errors.py,
    // RATELIMIT_HINWEIS) — dieses Literal greift nur, wenn ein 429 ohne Body
    // oder als HTML-Fehlerseite ankommt (z.B. aus einem vorgelagerten
    // Reverse-Proxy). Es bleibt deutsch wie GENERIC: die Datei wird statisch
    // ausgeliefert und kennt die Sprache des Nutzers nicht.
    var RATE_LIMITED = "Zu viele Anfragen in kurzer Zeit. Bitte kurz warten und es erneut versuchen.";
    var AUTO_DISMISS_MS = 6000;

    /**
     * Wählt die Toast-Meldung. Kurze Klartext-Bodies (z.B. die 400er aus
     * retention/workitem_bulk, der 429-Hinweis aus handler403) werden direkt
     * gezeigt; HTML-Fehlerseiten (500/403/404 → enthalten "<") sowie
     * leere/überlange Bodies fallen auf eine Standardmeldung zurück. Die
     * Ausgabe landet via textContent im DOM und ist damit ohnehin gegen
     * HTML-Injection geschützt.
     *
     * Refs #1873: Der Rückfall ist statusabhängig. Zuvor las der Handler
     * ``xhr.status`` nirgends aus — ein Rate-Limit war für den Nutzer von
     * einem Serverfehler nicht zu unterscheiden.
     */
    function _messageFor(xhr) {
        var status = xhr && typeof xhr.status === "number" ? xhr.status : 0;
        try {
            var body = xhr && xhr.responseText ? xhr.responseText.trim() : "";
            if (body && body.length <= 200 && body.indexOf("<") === -1) {
                return body;
            }
        } catch (_e) {
            // ignore — Fallback unten
        }
        return status === 429 ? RATE_LIMITED : GENERIC;
    }

    function _showErrorToast(message) {
        var toast = document.getElementById(TOAST_ID);
        if (!toast) {
            toast = document.createElement("div");
            toast.id = TOAST_ID;
            toast.setAttribute("role", "status");
            toast.setAttribute("aria-live", "polite");
            toast.setAttribute("data-testid", "htmx-error-toast");
            toast.className =
                "fixed bottom-4 right-4 z-50 max-w-sm bg-red-600 text-white " +
                "rounded-lg shadow-lg p-4 flex items-start gap-3";

            var text = document.createElement("span");
            text.className = "text-sm flex-grow";
            text.setAttribute("data-toast-text", "");
            toast.appendChild(text);

            var dismissBtn = document.createElement("button");
            dismissBtn.type = "button";
            dismissBtn.setAttribute("aria-label", "Schließen");
            dismissBtn.className = "text-white/70 hover:text-white text-lg leading-none";
            dismissBtn.textContent = "×";
            dismissBtn.addEventListener("click", function () {
                toast.remove();
            });
            toast.appendChild(dismissBtn);

            document.body.appendChild(toast);
        }

        toast.querySelector("[data-toast-text]").textContent = message;

        if (toast._dismissTimer) {
            window.clearTimeout(toast._dismissTimer);
        }
        toast._dismissTimer = window.setTimeout(function () {
            toast.remove();
        }, AUTO_DISMISS_MS);
    }

    /*
     * Refs #2067 (Cross-Review Finding 1): Archivierung der Einrichtung.
     *
     * HTMX-Antworten reicht der Service Worker direkt vom Netz weiter — bis
     * hierher wertete auf diesem Weg niemand den Marker aus, und der
     * Zugriffsentzug haette allein an ``Clear-Site-Data`` gehangen (in Safari
     * unvollstaendig umgesetzt, siehe docs/security-notes.md).
     *
     * Geprueft wird ausdruecklich Status UND Marker-Header, nie der Status
     * allein: ein nacktes 403 ist CSRF-/Rate-Limit-/Proxy-Rauschen und darf
     * den Aussendienst-Cache nicht leeren (Refs #1354). Genau diese
     * Unterscheidung pinnt die vitest-Suite in beide Richtungen.
     */
    var ARCHIVED_HEADER = "X-Facility-Archived";

    function _isFacilityArchived(xhr) {
        if (!xhr || xhr.status !== 403 || typeof xhr.getResponseHeader !== "function") {
            return false;
        }
        try {
            return xhr.getResponseHeader(ARCHIVED_HEADER) === "1";
        } catch (_e) {
            return false;
        }
    }

    function _purgeOfflineState() {
        // Regelfall: das gemeinsame Modul (facility-archived-purge.js) ist in
        // base.html vor dieser Datei geladen. Der Rueckfall greift auf
        // Layouts, die es nicht mitbringen — dort ist der Offline-Verbund
        // seinerseits vorhanden, also bleibt der Wipe wirksam.
        try {
            if (window.facilityArchivedPurge) {
                return window.facilityArchivedPurge.run();
            }
            if (window.crypto_session && window.crypto_session.wipeOfflineState) {
                return Promise.resolve(window.crypto_session.wipeOfflineState());
            }
        } catch (_e) {
            // ignore — der Redirect unten muss trotzdem stattfinden.
        }
        return Promise.resolve();
    }

    function _handleArchivedFacility() {
        // Neu laden statt Toast: JEDER Pfad antwortet ab jetzt mit der
        // Sperrseite, der Reload landet also genau dort. Ein Toast neben einer
        // weiterhin bedienbaren Oberflaeche waere die falsche Botschaft — der
        // Zugang ist weg, nicht die eine Aktion fehlgeschlagen.
        _purgeOfflineState()
            .catch(function () {})
            .then(function () {
                try {
                    window.location.reload();
                } catch (_e) {
                    // ignore — der naechste Klick landet ohnehin auf der Sperrseite.
                }
            });
    }

    // htmx:responseError feuert bei 4xx/5xx-Serverantworten und bubbelt zum
    // body. (Netzwerk-/Sende-Fehler im Offline-Fall behandelt der Service
    // Worker + Offline-Banner separat — daher hier bewusst nur responseError.)
    document.body.addEventListener("htmx:responseError", function (event) {
        var xhr = event && event.detail ? event.detail.xhr : null;
        if (_isFacilityArchived(xhr)) {
            _handleArchivedFacility();
            return;
        }
        _showErrorToast(_messageFor(xhr));
    });
})();
