/*
 * CSRF-Helper. Liest den aktuellen CSRF-Token aus dem <meta name="csrf-token">-
 * Tag, den das Basistemplate (und die Auth-Templates) im <head> rendern.
 *
 * Hintergrund: Mit CSRF_COOKIE_HTTPONLY=True (Refs #602) kann JavaScript den
 * csrftoken-Cookie nicht mehr lesen. Damit der Offline-Queue-Replay, das
 * Offline-Bundle-Fetching und der Auth-Bootstrap weiterhin den korrekten Token
 * an das Backend schicken können, rendert das Template den Wert explizit ins
 * Meta-Tag — identisch zu dem, was Django via csrf_token()-Template-Tag
 * ohnehin im Response mitschickt.
 *
 * Die Funktion ist absichtlich synchron und ohne Dependencies, damit sie vor
 * den Offline-Modulen geladen werden kann, ohne Ladeordnung komplizierter zu
 * machen.
 */
(function () {
    "use strict";

    function getCsrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute("content") || "" : "";
    }

    window.getCsrfToken = getCsrfToken;
})();
