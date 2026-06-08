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
    var AUTO_DISMISS_MS = 6000;

    /**
     * Wählt die Toast-Meldung. Kurze Klartext-Bodies (z.B. die 400er aus
     * retention/workitem_bulk) werden direkt gezeigt; HTML-Fehlerseiten
     * (500/403/404 → enthalten "<") sowie leere/überlange Bodies fallen auf die
     * generische Meldung zurück. Die Ausgabe landet via textContent im DOM und
     * ist damit ohnehin gegen HTML-Injection geschützt.
     */
    function _messageFor(xhr) {
        try {
            var body = xhr && xhr.responseText ? xhr.responseText.trim() : "";
            if (body && body.length <= 200 && body.indexOf("<") === -1) {
                return body;
            }
        } catch (_e) {
            // ignore — Fallback unten
        }
        return GENERIC;
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

    // htmx:responseError feuert bei 4xx/5xx-Serverantworten und bubbelt zum
    // body. (Netzwerk-/Sende-Fehler im Offline-Fall behandelt der Service
    // Worker + Offline-Banner separat — daher hier bewusst nur responseError.)
    document.body.addEventListener("htmx:responseError", function (event) {
        var xhr = event && event.detail ? event.detail.xhr : null;
        _showErrorToast(_messageFor(xhr));
    });
})();
