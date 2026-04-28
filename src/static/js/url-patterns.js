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
    self.URL_PATTERNS = {
        EVENT_NEW: /\/events\/new\//,
        EVENT_EDIT: new RegExp("/events/" + UUID + "/edit/", "i"),
        WORKITEM_NEW: /\/workitems\/new\//,
        WORKITEM_EDIT: new RegExp("/workitems/" + UUID + "/edit/", "i"),
        CLIENT_DETAIL: CLIENT_DETAIL_RE,
    };
    self.URL_PATTERNS.QUEUE_PATTERNS = [
        self.URL_PATTERNS.EVENT_NEW,
        self.URL_PATTERNS.EVENT_EDIT,
        self.URL_PATTERNS.WORKITEM_NEW,
        self.URL_PATTERNS.WORKITEM_EDIT,
    ];
    self.URL_PATTERNS.extractClientPk = function (url) {
        var m = CLIENT_DETAIL_RE.exec(url);
        return m ? m[1] : null;
    };
})();
