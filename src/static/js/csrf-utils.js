/*
 * Gemeinsames CSRF-Util fuer die Offline-Replay-Konsumenten (Refs #1408).
 *
 * Ersetzt die drei duplizierten ``_csrfFromMeta()``- und die zwei
 * ``_refreshCsrf()``-Kopien in offline-client.js/offline-edit.js/
 * offline-queue.js. Der fruehere ``_refreshCsrf`` holte eine ``/login/``-Seite
 * und parste den frischen Token per Regex aus dem HTML — genau die fragile
 * Konstruktion, die in #1330/#1332 (stale-CSRF-403 beim Retry) Bugquelle war.
 * ``refresh()`` bezieht den Token stattdessen aus dem dedizierten JSON-Endpoint
 * ``/api/v1/offline/csrf/`` (``core:offline_csrf``).
 *
 * Klassisches Skript/IIFE (keine ES-Module), im Stil der Nachbarmodule:
 * exponiert ``window.csrfUtils = { fromMeta(), refresh() }``. Muss VOR den
 * Konsumenten geladen werden (base.html + die Offline-Shell-Templates erben die
 * Ladereihenfolge ueber base.html).
 */
(function () {
    "use strict";

    // Muss zur URL in core/urls.py (name="offline_csrf") passen.
    var CSRF_ENDPOINT = "/api/v1/offline/csrf/";

    function fromMeta() {
        // Refs #602: CSRF_COOKIE_HTTPONLY verbietet JS-Zugriff aufs Cookie,
        // der Token kommt aus dem <meta name="csrf-token">-Tag im Basistemplate.
        if (typeof window.getCsrfToken === "function") {
            return window.getCsrfToken() || null;
        }
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute("content") || null : null;
    }

    async function refresh() {
        // Frischen Token vom dedizierten Endpoint holen (statt HTML-Scrape).
        // Bei Erfolg zusaetzlich das <meta name="csrf-token"> aktualisieren,
        // damit nachfolgende fromMeta()-Reads konsistent bleiben. Bei
        // !ok/Netzwerkfehler null zurueckgeben — die Aufrufer behalten dann
        // ihre bestehende Fehler-/revoked-Klassifikation.
        try {
            var resp = await fetch(CSRF_ENDPOINT, {
                method: "GET",
                credentials: "same-origin",
                headers: { Accept: "application/json" },
            });
            if (resp.ok) {
                var data = await resp.json();
                var token = data && data.csrftoken;
                if (token) {
                    var meta = document.querySelector('meta[name="csrf-token"]');
                    if (meta) meta.setAttribute("content", token);
                    return token;
                }
            }
        } catch (_e) {
            // Netz weg / Fehler — null, siehe Kommentar oben.
        }
        return null;
    }

    window.csrfUtils = {
        fromMeta: fromMeta,
        refresh: refresh,
    };
})();
