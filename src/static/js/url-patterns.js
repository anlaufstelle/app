/*
 * Single source of truth for URL patterns shared between the Service Worker
 * and the offline-queue module. UUIDs use the canonical 8-4-4-4-12 hex form.
 *
 * Loaded via <script src="..."> in the document context and via
 * importScripts(...) in the Service Worker context. `self` is the global in
 * both, so the assignment works in either environment.
 */
(function () {
    "use strict";
    var UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
    var CLIENT_DETAIL_RE = new RegExp("/clients/(" + UUID + ")/(?:$|\\?)", "i");
    // Refs #1533 (#1499, SI-5): die kanonische Personenlisten-URL /clients/
    // (mit optionalem ?query). Bewusst OHNE ^-Anker (request.url ist eine volle
    // URL) und mit exaktem Terminator ``(?:$|\?)`` — genau wie CLIENT_DETAIL_RE,
    // nur ohne pk-Segment. So matcht GENAU /clients/ und /clients/?…, aber NICHT
    // /clients/new/, /clients/<uuid>/, /clients/trash/, /partials/clients/
    // autocomplete/ oder /offline/clients/<uuid>/ (nach /clients/ folgt dort ein
    // Segment, kein Terminator). Der SW serviert offline an dieser URL die
    // gecachte, pk-lose Listen-Shell IN-PLACE (kein Redirect).
    var CLIENT_LIST_RE = new RegExp("/clients/(?:$|\\?)", "i");
    // Refs #1396: pk-behaftete Konflikt-Review-URL (/offline/conflicts/<uuid>/).
    // Bewusst mit dem /offline/conflicts/-Praefix, damit die pk-lose Liste
    // (/offline/conflicts/) NICHT matcht und extractClientPk Konflikt-URLs
    // ebenso wenig faengt (kein /clients/-Segment).
    var CONFLICT_REVIEW_RE = new RegExp("/offline/conflicts/(" + UUID + ")/(?:$|\\?)", "i");
    self.URL_PATTERNS = {
        EVENT_NEW: /\/events\/new\//,
        EVENT_EDIT: new RegExp("/events/" + UUID + "/edit/", "i"),
        WORKITEM_NEW: /\/workitems\/new\//,
        WORKITEM_EDIT: new RegExp("/workitems/" + UUID + "/edit/", "i"),
        // Refs #1419: Status-Uebergaenge (Uebernehmen/Erledigt/Verwerfen/
        // Zuruecksetzen) — HTMX-POSTs der Inbox-Karten bzw. Formular-POSTs
        // der Detailseite, unter dem /partials/-Praefix (urls.py).
        WORKITEM_STATUS: new RegExp("/partials/workitems/" + UUID + "/status/", "i"),
        CLIENT_DETAIL: CLIENT_DETAIL_RE,
        CLIENT_LIST: CLIENT_LIST_RE,
        CONFLICT_REVIEW: CONFLICT_REVIEW_RE,
        // Refs #751: Attachment-Downloads liefern Binärdaten, dürfen nicht
        // durch die HTML-Offline-Fallback-Kette ersetzt werden (sonst sieht
        // der User die /offline/-Seite statt einer Datei oder eines
        // Berechtigungs-/404-Fehlers).
        ATTACHMENT_DOWNLOAD: new RegExp(
            "/events/" + UUID + "/attachments/" + UUID + "/download/",
            "i"
        ),
        // Auch Datenauskunft-Exporte (Art. 15 PDF / Art. 20 JSON) und
        // Statistik-Exporte sind Downloads, die offline nicht sinnvoll
        // ersetzbar sind.
        EXPORT_DOWNLOAD: /\/(?:export|statistics\/export|clients\/[^/]+\/export)/i,
    };
    self.URL_PATTERNS.QUEUE_PATTERNS = [
        self.URL_PATTERNS.EVENT_NEW,
        self.URL_PATTERNS.EVENT_EDIT,
        self.URL_PATTERNS.WORKITEM_NEW,
        self.URL_PATTERNS.WORKITEM_EDIT,
        self.URL_PATTERNS.WORKITEM_STATUS,
    ];
    self.URL_PATTERNS.extractClientPk = function (url) {
        var m = CLIENT_DETAIL_RE.exec(url);
        return m ? m[1] : null;
    };
    // Refs #1396: analog extractClientPk — die event-pk aus einer
    // Konflikt-Review-URL ziehen (der SW serviert dafuer offline den pk-losen
    // conflict-shell IN-PLACE).
    self.URL_PATTERNS.extractConflictPk = function (url) {
        var m = CONFLICT_REVIEW_RE.exec(url);
        return m ? m[1] : null;
    };
})();
