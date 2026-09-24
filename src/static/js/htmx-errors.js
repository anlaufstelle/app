/*
 * Globaler HTMX-Fehler-Handler (Refs #1016, Workstream C — C2).
 *
 * Ohne Handler bliebe eine fehlgeschlagene hx-Aktion, deren Antwort nicht
 * eingewechselt wird, für den Nutzer komplett unsichtbar. Dieser Handler
 * zeigt dann einen diskreten Fehler-Toast unten rechts.
 *
 * Refs #2232/#2321: Für 4xx gilt die Swap-Regel aus der
 * ``responseHandling``-Tabelle in base.html — fachliche 4xx-Partials und
 * -Klartexte (z. B. die 409 der Vorschlagskarte in retention.py) landen im
 * auslösenden Ziel und tragen ihre Meldung selbst; der Toast unterbleibt
 * dann (siehe ``swappedErrorResponses`` weiter unten). Ausnahme: Antworten
 * mit ``HX-Reswap: none`` (Refs #2358, ``htmx_absage`` in
 * core/views/mixins.py, z. B. die Absage bei Wirkungszielen und
 * Meilensteinen) werden nicht eingewechselt — der Kasten bleibt stehen, und
 * der Toast zeigt die Meldung. Ohne Swap — 401/403/404/405/422/429, alle
 * 5xx und ``HX-Reswap: none`` — ist er der einzige Weg, auf dem der
 * Fehlschlag sichtbar wird.
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
    // Refs #2358: Obergrenze für Klartext-Bodies, die der Toast wörtlich
    // zeigt. Fachliche Absagen mit ``HX-Reswap: none`` erreichen die Nutzerin
    // nur hier; die Art.-18-Meldung ist länger als die frühere Grenze von 200
    // Zeichen. Längere Bodies fallen weiter auf die Standardmeldung zurück.
    var MAX_PLAIN_TEXT_LENGTH = 400;
    // Refs #2232: Ein geswapptes 4xx-Partial enthaelt seine sichtbare
    // Fehlermeldung bereits im fachlichen Ziel. htmx feuert wegen ``error:true``
    // danach trotzdem responseError (wichtig fuer lokale Listener wie den
    // Reorder-Reload). Nur DIESER globale Toast darf dann nicht zusaetzlich
    // erscheinen. WeakSet bindet den Marker an genau die eine XHR-Instanz.
    var swappedErrorResponses = new WeakSet();

    function _swapStyleIsNone(detail) {
        if (detail.swapOverride === "none") {
            return true;
        }
        var elt = detail.requestConfig && detail.requestConfig.elt;
        while (elt && elt.getAttribute) {
            var value = elt.getAttribute("hx-swap") || elt.getAttribute("data-hx-swap");
            if (value) {
                return value.trim().split(/\s+/)[0] === "none";
            }
            elt = elt.parentElement;
        }
        return false;
    }

    /*
     * Refs #2321 (H-4): 404 und 405 fallen seit #2232 unter die
     * ``4..``-Swap-Regel — und das passt fuer beide nicht. Eine 404 liefert
     * ein ganzes HTML-Dokument (Fehlerseite), das in ein kleines fachliches
     * Ziel eingewechselt wuerde; eine 405 hat einen leeren Rumpf und LEERT
     * das Ziel. Kein Datenabfluss, aber ein Oberflaechenzustand, aus dem die
     * Nutzerin ohne Neuladen nicht herauskommt. Beide werden deshalb wie 403
     * gefuehrt: kein Swap, dafuer der globale Fehler-Toast (der unten genau
     * dann erscheint, wenn NICHT geswappt wurde).
     *
     * Die Regel steht hier und nicht in der ``responseHandling``-Tabelle von
     * base.html, damit sie fuer jedes Layout gilt, das diese Datei laedt.
     */
    var NEVER_SWAPPED_STATUSES = [404, 405];

    document.body.addEventListener("htmx:beforeSwap", function (event) {
        var detail = event && event.detail ? event.detail : {};
        var xhr = detail.xhr;
        var status = xhr && typeof xhr.status === "number" ? xhr.status : 0;
        if (NEVER_SWAPPED_STATUSES.indexOf(status) !== -1) {
            detail.shouldSwap = false;
            return;
        }
        if (status >= 400 && status < 500 && detail.shouldSwap === true && !_swapStyleIsNone(detail)) {
            swappedErrorResponses.add(xhr);
        }
    });

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
            if (body && body.length <= MAX_PLAIN_TEXT_LENGTH && body.indexOf("<") === -1) {
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

    // Refs #2360 (Vertrag aus sw-register.js, Refs #1951): dieselbe
    // Logout-Marke (``window.__logoutInFlight``) wie beim SW-Reload-Gate —
    // ein Schnell-Verlassen, das waehrend des asynchronen Purges unten
    // (``_purgeOfflineState()``) startet, darf den anschliessenden Reload
    // nicht mehr auslösen; die Weiterleitung nach ``/login/`` hat Vorrang.
    var _LOGOUT_NAV_GRACE_MS = 10000;

    function _logoutNavInFlight() {
        var startedAt = window.__logoutInFlight;
        return typeof startedAt === "number" && Date.now() - startedAt < _LOGOUT_NAV_GRACE_MS;
    }

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

    // Refs #2067 (Cross-Review NF1): derselbe Deckel wie im Purge-Modul und
    // beim Logout-Wipe (sw-register.js). Er sichert hier den RUECKFALL-Pfad —
    // ``facilityArchivedPurge.run()`` deckelt sich selbst, ein direktes
    // ``wipeOfflineState()`` nicht. Ohne ihn haengt der Reload unten an einem
    // klemmenden Purge, und die Nutzerin bliebe auf einer Oberflaeche stehen,
    // deren Zugang gerade entzogen wurde.
    var PURGE_TIMEOUT_MS = 3000;

    function _purgeOfflineState() {
        // Regelfall: das gemeinsame Modul (facility-archived-purge.js) ist in
        // base.html vor dieser Datei geladen. Der Rueckfall greift auf
        // Layouts, die es nicht mitbringen — dort ist der Offline-Verbund
        // seinerseits vorhanden, also bleibt der Wipe wirksam.
        var running;
        try {
            if (window.facilityArchivedPurge) {
                running = Promise.resolve(window.facilityArchivedPurge.run());
            } else if (window.crypto_session && window.crypto_session.wipeOfflineState) {
                running = Promise.resolve(window.crypto_session.wipeOfflineState());
            } else {
                return Promise.resolve();
            }
        } catch (_e) {
            // synchroner Wurf — der Redirect unten muss trotzdem stattfinden.
            return Promise.resolve();
        }
        var timeout = new Promise(function (resolve) {
            window.setTimeout(resolve, PURGE_TIMEOUT_MS);
        });
        return Promise.race([running.catch(function () {}), timeout]);
    }

    function _handleArchivedFacility() {
        // Neu laden statt Toast: JEDER Pfad antwortet ab jetzt mit der
        // Sperrseite, der Reload landet also genau dort. Ein Toast neben einer
        // weiterhin bedienbaren Oberflaeche waere die falsche Botschaft — der
        // Zugang ist weg, nicht die eine Aktion fehlgeschlagen.
        _purgeOfflineState()
            .catch(function () {})
            .then(function () {
                // Refs #2360: unmittelbar vor dem Reload noch einmal
                // pruefen — die Marke kann sich waehrend des Purges
                // (asynchron) geaendert haben.
                if (_logoutNavInFlight()) return;
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
        if (xhr && swappedErrorResponses.has(xhr)) {
            swappedErrorResponses.delete(xhr);
            return;
        }
        if (_isFacilityArchived(xhr)) {
            _handleArchivedFacility();
            return;
        }
        _showErrorToast(_messageFor(xhr));
    });
})();
