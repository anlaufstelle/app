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
    // Refs #1533 (#1499): die kanonische Personenlisten-URL /clients/
    // (mit optionalem ?query). Bewusst OHNE ^-Anker (request.url ist eine volle
    // URL) und mit exaktem Terminator ``(?:$|\?)`` — genau wie CLIENT_DETAIL_RE,
    // nur ohne pk-Segment. So matcht GENAU /clients/ und /clients/?…, aber NICHT
    // /clients/new/, /clients/<uuid>/, /clients/trash/, /partials/clients/
    // autocomplete/ oder /offline/clients/<uuid>/ (nach /clients/ folgt dort ein
    // Segment, kein Terminator). Der SW serviert offline an dieser URL die
    // gecachte, pk-lose Listen-Shell IN-PLACE (kein Redirect).
    var CLIENT_LIST_RE = new RegExp("/clients/(?:$|\\?)", "i");
    // Refs #1543 (#1499): die kanonische Aufgaben-Inbox-URL /workitems/
    // (mit optionalem ?query). Exakter Terminator ``(?:$|\?)`` wie CLIENT_LIST_RE:
    // matcht GENAU /workitems/ und /workitems/?…, aber NICHT /workitems/new/,
    // /workitems/<uuid>/edit/ oder /partials/workitems/<uuid>/status/ (dort folgt
    // nach /workitems/ ein Segment, kein Terminator). Der SW serviert offline an
    // dieser URL die pk-lose Aufgaben-Listen-Shell IN-PLACE (kein Redirect).
    var WORKITEM_LIST_RE = new RegExp("/workitems/(?:$|\\?)", "i");
    // Refs #1396: pk-behaftete Konflikt-Review-URL (/offline/conflicts/<uuid>/).
    // Bewusst mit dem /offline/conflicts/-Praefix, damit die pk-lose Liste
    // (/offline/conflicts/) NICHT matcht und extractClientPk Konflikt-URLs
    // ebenso wenig faengt (kein /clients/-Segment).
    var CONFLICT_REVIEW_RE = new RegExp("/offline/conflicts/(" + UUID + ")/(?:$|\\?)", "i");
    self.URL_PATTERNS = {
        // Schreibpfade sind auf den VOLLSTAENDIGEN pathname geankert. Der
        // Service Worker prueft vor diesen Regexen zusaetzlich die Origin.
        // Dadurch matchen weder /partials/tally/increment/extra/ noch
        // /partials/tally/increment-evil/; auch
        // https://evil.example/partials/tally/increment/ bleibt ausgeschlossen.
        EVENT_NEW: /^\/events\/new\/$/,
        EVENT_EDIT: new RegExp("^/events/" + UUID + "/edit/$", "i"),
        WORKITEM_NEW: /^\/workitems\/new\/$/,
        WORKITEM_EDIT: new RegExp("^/workitems/" + UUID + "/edit/$", "i"),
        // Refs #1419: Status-Uebergaenge (Uebernehmen/Erledigt/Verwerfen/
        // Zuruecksetzen) — HTMX-POSTs der Inbox-Karten bzw. Formular-POSTs
        // der Detailseite, unter dem /partials/-Praefix (urls.py).
        WORKITEM_STATUS: new RegExp("^/partials/workitems/" + UUID + "/status/$", "i"),
        // #1912: einziger Strichlisten-Schreibpfad; GET und POST teilen die
        // Route, gequeued werden im SW weiterhin ausschliesslich POST/PUT.
        TALLY_INCREMENT: /^\/partials\/tally\/increment\/$/,
        // Refs #1476: Die Konfliktliste legt diesen Audit-POST direkt in die
        // verschluesselte Queue. Er muss dieselbe exakte Replay-Policy
        // durchlaufen wie vom SW abgefangene Schreibrequests.
        OFFLINE_AUDIT_NOTE_EXPORT: /^\/api\/v1\/offline\/audit\/note-export\/$/,
        CLIENT_DETAIL: CLIENT_DETAIL_RE,
        CLIENT_LIST: CLIENT_LIST_RE,
        WORKITEM_LIST: WORKITEM_LIST_RE,
        CONFLICT_REVIEW: CONFLICT_REVIEW_RE,
        // Refs #751: Attachment-Downloads liefern Binärdaten, dürfen nicht
        // durch die HTML-Offline-Fallback-Kette ersetzt werden (sonst sieht
        // der User die /offline/-Seite statt einer Datei oder eines
        // Berechtigungs-/404-Fehlers).
        //
        // M24/AP5 (Refs #1738, ADR-039 §8): Anhänge hängen seit dem
        // Owner-Umbau an Ereignis, Person ODER Fall — es gibt drei
        // Download-Routen mit identischem Zuschnitt. Ohne die Erweiterung um
        // ``clients|cases`` fielen genau die neuen Downloads in die
        // Fallback-Kette und der Benutzer sähe offline eine Ersatzseite statt
        // eines Fehlers.
        ATTACHMENT_DOWNLOAD: new RegExp(
            "/(?:events|clients|cases)/" + UUID + "/attachments/" + UUID + "/download/",
            "i"
        ),
        // Auch Datenauskunft-Exporte (Art. 15 PDF / Art. 20 JSON) und
        // Statistik-Exporte sind Downloads, die offline nicht sinnvoll
        // ersetzbar sind.
        EXPORT_DOWNLOAD: /\/(?:export|statistics\/export|clients\/[^/]+\/export)/i,
    };
    // WARNUNG (M24/AP5, Refs #1738, ADR-039 §8): Personen- und Fall-Formulare
    // gehören hier bewusst NICHT hinein. Beide sind seit dem Owner-Umbau
    // ``multipart/form-data`` (Datei-Felder an Personen-/Fall-Typen), und der
    // Service Worker queued multipart-POSTs grundsätzlich nicht (binäre Blobs,
    // Refs #567). Ein Eintrag hier ergäbe einen toten Queue-Pfad — genau die
    // Falle, die #1489 im Event-Formular aufgeräumt hat. Voraussetzung für eine
    // spätere Aufnahme ist das Enctype-Downgrade (``event-form-enctype.js``),
    // das an beiden Formularen bereits anliegt und dort auch greift (ohne
    // gewählte Datei geht der Submit als ``urlencoded`` raus) — serverseitig
    // folgenlos, weil Django beide Kodierungen gleich parst.
    self.URL_PATTERNS.QUEUE_PATTERNS = [
        self.URL_PATTERNS.EVENT_NEW,
        self.URL_PATTERNS.EVENT_EDIT,
        self.URL_PATTERNS.WORKITEM_NEW,
        self.URL_PATTERNS.WORKITEM_EDIT,
        self.URL_PATTERNS.WORKITEM_STATUS,
        self.URL_PATTERNS.TALLY_INCREMENT,
        self.URL_PATTERNS.OFFLINE_AUDIT_NOTE_EXPORT,
    ];
    self.URL_PATTERNS.matchesPath = function (pattern, url) {
        try {
            return pattern.test(new URL(url, self.location.origin).pathname);
        } catch (e) {
            return false;
        }
    };
    self.URL_PATTERNS.isQueueableWriteUrl = function (url) {
        try {
            var parsed = new URL(url, self.location.origin);
            // Origin-Gate bewusst VOR den exakten Path-Regexen. Diese Policy
            // gilt gemeinsam fuer SW, Enqueue und Replay.
            if (parsed.origin !== self.location.origin) return false;
            return self.URL_PATTERNS.QUEUE_PATTERNS.some(function (pattern) {
                return pattern.test(parsed.pathname);
            });
        } catch (e) {
            return false;
        }
    };
    self.URL_PATTERNS.isQueueableWriteMethod = function (method) {
        try {
            var normalized = String(method).toUpperCase();
            return normalized === "POST" || normalized === "PUT";
        } catch (e) {
            return false;
        }
    };
    self.URL_PATTERNS.isQueueableWriteRequest = function (url, method) {
        return (
            self.URL_PATTERNS.isQueueableWriteMethod(method) &&
            self.URL_PATTERNS.isQueueableWriteUrl(url)
        );
    };
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
    // Refs #1543 (#1499): exakter Wurzel-URL-Test fuer den Zeitstrom-
    // Fallback (pathname === "/"). Bewusst KEIN Regex auf request.url — nur der
    // geparste Pfad ist eindeutig "die Startseite" (nicht /clients/ o.ae.). Der
    // SW serviert offline an / die Zeitstrom-Chronik-Shell IN-PLACE.
    self.URL_PATTERNS.isZeitstromRoot = function (url) {
        try {
            return new URL(url).pathname === "/";
        } catch (e) {
            return false;
        }
    };
})();
