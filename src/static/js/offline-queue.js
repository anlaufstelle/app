/*
 * Offline-Queue: receives QUEUE_REQUEST messages from the Service Worker,
 * stores the request body encrypted at rest in IndexedDB (via offline-store
 * + crypto_session), and replays the queue when the network returns.
 *
 * Refs #573, #576.
 *
 * Failure modes that this module guards against:
 *   - missing CryptoKey (e.g. session timed out): rejects the enqueue so
 *     the user sees an explicit error instead of a silent leak to plaintext
 *     localStorage
 *   - 4xx replay (Refs #1351/#1384, HTTP-Replay-Contract — classification
 *     lives in `replayQueue`): 409 -> `localStatus: "conflict"` (excluded
 *     from auto-replay); 422/400/404/410 -> `localStatus: "dead"`
 *     (dead-letter, no Head-of-Line-Blocking — the next queued record is
 *     still sent); 403 -> one CSRF-refresh retry, then dead; an auth
 *     redirect mid-batch (Refs #2289: login, MFA setup/verify or password
 *     change — followed redirect, ``HX-Redirect`` header or the ``Location``
 *     of an unfollowed 3xx) halts the loop WITHOUT deleting the record
 *     ("auth-pending"); an unexpected bare 200 also becomes dead instead of
 *     being silently treated as success.
 *   - replay success (Refs #2289): a write only counts as saved when the
 *     response carries the server's confirmation marker
 *     ``X-Offline-Replay-Result: saved``. Queue targets without marker
 *     support (workitem status, tally, audit note export) keep the previous
 *     redirect/HTMX contract — but only after the auth classification.
 *   - 429 replay: exponential backoff via `retryAfter` + batch abort
 *     (rate-limit is user-global, not per-record).
 *   - 5xx replay: exponential backoff via `retryAfter`, batch abort.
 *   - multipart/form-data: rejected before enqueue (the SW already returns
 *     503 for this case, this is the defence in depth)
 */
(function () {
    "use strict";

    const MAX_BACKOFF_MS = 30 * 60 * 1000; // 30 min
    const BASE_BACKOFF_MS = 60 * 1000; // 1 min

    function _store() {
        if (!window.offlineStore) {
            throw new Error("OfflineStoreNotLoaded");
        }
        return window.offlineStore;
    }

    // Refs #1408: die gemeinsame CSRF-Logik (fromMeta/refresh) lebt in
    // csrf-utils.js (window.csrfUtils). Zur CALL-Zeit aufloesen und tolerant
    // bleiben, falls das Util wider Erwarten fehlt (kein Crash).
    function _csrfFromMeta() {
        return window.csrfUtils ? window.csrfUtils.fromMeta() : null;
    }

    function _newIdempotencyKey() {
        // Refs #1109 (F-09): Stabiler Schlüssel pro Queue-Eintrag. Bricht beim
        // Replay die Verbindung nach erfolgreichem Server-Write ab, wird die
        // Zeile erneut gespielt — derselbe Schlüssel lässt den Server den
        // Doppel-Submit erkennen (Header ``X-Idempotency-Key``).
        if (window.crypto && typeof window.crypto.randomUUID === "function") {
            return window.crypto.randomUUID();
        }
        // Fallback für ältere Engines ohne randomUUID: zeit- + zufallsbasiert.
        return "idem-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
    }

    function _incomingIdempotencyKey(headers) {
        // Refs #1351 (Bug #1): Der Service Worker mintet den Idempotency-Key
        // bereits fuer den ERSTVERSUCH-fetch (sw.js) und reicht ihn in den
        // QUEUE_REQUEST-Headern durch. Diesen bevorzugen (case-insensitiv —
        // die Fetch-Headers-API normalisiert Keys auf lowercase), damit
        // Erstversuch UND Replay DENSELBEN Key tragen und der Server einen
        // Slow-Commit gegen den Replay deduplizieren kann. Nur wenn keiner
        // uebergeben wurde, hier neu minten.
        if (!headers) return null;
        for (const k of Object.keys(headers)) {
            if (k.toLowerCase() === "x-idempotency-key" && headers[k]) return headers[k];
        }
        return null;
    }

    async function _refreshCsrf() {
        // Refs #1408: holt den frischen Token vom dedizierten Endpoint
        // (window.csrfUtils.refresh) statt per Regex aus gescraptem
        // /login/-HTML (#1330/#1332). Bei !ok/Netzfehler null — der 403-Retry
        // in replayQueue unterbleibt dann sauber (kein stale-Token-Retry).
        return window.csrfUtils ? window.csrfUtils.refresh() : null;
    }

    /*
     * Refs #2289 — Vertrag „Erfolgsbestaetigung" (wortgleich in
     * offline-edit.js; die beiden Module teilen bewusst keinen Namensraum,
     * siehe _csrfFromMeta/_refreshCsrf. tests/js/offline-replay-auth.test.mjs
     * haelt beide Fassungen deckungsgleich):
     *
     * (1) Erfolg NUR mit Marker ``X-Offline-Replay-Result: saved``, den allein
     *     die Schreib-Endpunkte NACH einem festgeschriebenen Schreibvorgang
     *     setzen. Fuer Queue-Ziele OHNE Markerunterstuetzung (Status-Wechsel,
     *     Strichliste, Audit-Notiz-Export) bleibt der bisherige Redirect-/
     *     HTMX-Kontrakt in Kraft — aber erst NACH der Auth-Klassifikation.
     * (2) Antworten, die auf ``/login/``, ``/mfa/`` (Setup UND Verify),
     *     ``/password-change/`` oder ``/password-reset/`` fuehren — gefolgter
     *     Redirect, ``HX-Redirect``-Header oder ``Location`` eines ungefolgten
     *     3xx — lassen die Zeile UNVERAENDERT und halten den Batch an.
     *
     * Bis #2289 pruefte die Queue nur ``/login/``: eine MFA-Weiterleitung
     * (Pflicht waehrend der Offline-Phase geaendert, abgelaufene
     * Einrichtungsfrist) galt als Erfolg und die Zeile wurde geloescht.
     */
    const AUTH_PENDING_PREFIXES = ["/login/", "/mfa/", "/password-change/", "/password-reset/"];
    // Refs #2289 (wortgleich in offline-edit.js): der erzwungene
    // Passwortwechsel rotiert den Schluessel-Salt und loescht den lokalen
    // Bestand — die Anzeige braucht die Zielklasse, nicht nur „ausstehend".
    const AUTH_PENDING_PASSWORD_PREFIXES = ["/password-change/", "/password-reset/"];
    // Protokollversion des Replays: "2" = dieser Client erwartet die
    // Erfolgsbestaetigung STATT des Redirects. Alte Clients (v48-Precache)
    // senden "1" und behalten den bisherigen Redirect-Kontrakt.
    const REPLAY_PROTOCOL_VERSION = "2";
    const REPLAY_RESULT_HEADER = "X-Offline-Replay-Result";
    const REPLAY_RESULT_SAVED = "saved";

    function _responseHeader(response, name) {
        try {
            if (!response || !response.headers || typeof response.headers.get !== "function") return null;
            return response.headers.get(name);
        } catch (_e) {
            return null;
        }
    }

    /** "login" | "password-change" | null — die Zielklasse eines Pfades. */
    function _authPendingKind(rawUrl) {
        if (!rawUrl) return null;
        let path;
        try {
            path = new URL(rawUrl, self.location.origin).pathname;
        } catch (_e) {
            path = String(rawUrl);
        }
        // Ein Ziel ohne abschliessenden Schraegstrich (``/mfa``) zaehlt mit.
        const normalized = path.endsWith("/") ? path : path + "/";
        if (!AUTH_PENDING_PREFIXES.some((prefix) => normalized.startsWith(prefix))) return null;
        return AUTH_PENDING_PASSWORD_PREFIXES.some((prefix) => normalized.startsWith(prefix))
            ? "password-change"
            : "login";
    }

    function _isAuthPendingTarget(rawUrl) {
        return _authPendingKind(rawUrl) !== null;
    }

    /** Zielklasse der Antwort oder null (die drei Kanaele in fester Reihenfolge). */
    function _authPendingResponseKind(response) {
        if (!response) return null;
        if (response.redirected) {
            const kind = _authPendingKind(response.url);
            if (kind) return kind;
        }
        // Refs #1419: die HtmxSession-Middleware kann einen Auth-Bounce fuer
        // HTMX-Requests als ``200 + HX-Redirect`` statt als 302 liefern.
        const hxKind = _authPendingKind(_responseHeader(response, "HX-Redirect"));
        if (hxKind) return hxKind;
        // Ungefolgtes 3xx (defensiv — der Replay folgt Redirects sonst).
        if (response.status >= 300 && response.status < 400) {
            const locationKind = _authPendingKind(_responseHeader(response, "Location"));
            if (locationKind) return locationKind;
        }
        return null;
    }

    function _isAuthPendingResponse(response) {
        return _authPendingResponseKind(response) !== null;
    }

    function _isSavedConfirmed(response) {
        return _responseHeader(response, REPLAY_RESULT_HEADER) === REPLAY_RESULT_SAVED;
    }

    // Refs #2289: Zustand an die Darstellung melden — der Empfaenger des
    // window-Events sitzt in offline-edit.js (laedt auf denselben Seiten,
    // direkt nach diesem Modul).
    function _signalAuthPending(pending, kind) {
        const active = Boolean(pending);
        Promise.resolve()
            .then(async () => {
                let count = 0;
                try {
                    const breakdown = (await _store().countQueueByStatus()) || {};
                    count += breakdown.pending || 0;
                } catch (_e) {
                    /* Zaehler bleibt bei dem, was sich lesen liess */
                }
                try {
                    count += (await _store().countUnsyncedEvents()) || 0;
                } catch (_e) {
                    /* dito */
                }
                window.dispatchEvent(
                    new CustomEvent("offline-auth-pending", {
                        detail: { pending: active, count: count, kind: active ? kind || "login" : null },
                    })
                );
            })
            .catch(() => {});
    }

    async function _updateQueueCount() {
        // Refs #1351/#1384: `pending`/`blocked` getrennt fuers M8-Badge;
        // `count` bleibt die Gesamtzahl fuer die Abwaertskompatibilitaet des
        // heutigen Banners (bis Task 4 auf pending/blocked umstellt).
        const breakdown = await _store().countQueueByStatus();
        window.dispatchEvent(
            new CustomEvent("offline-queue-count", {
                detail: { count: breakdown.total, pending: breakdown.pending, blocked: breakdown.blocked },
            })
        );
    }

    function _queueRequestBlockReason(url, method) {
        const patterns = (typeof self !== "undefined" && self.URL_PATTERNS) || null;
        if (!patterns || !patterns.isQueueableWriteUrl || !patterns.isQueueableWriteUrl(url)) {
            return "unsafe-url";
        }
        if (!patterns.isQueueableWriteMethod || !patterns.isQueueableWriteMethod(method)) {
            return "unsafe-method";
        }
        if (
            !patterns.isQueueableWriteRequest ||
            !patterns.isQueueableWriteRequest(url, method)
        ) {
            return "unsafe-request";
        }
        return null;
    }

    async function enqueueRequest(url, method, body, headers) {
        await window.crypto_session?.awaitReady?.();
        if (!window.crypto_session || !window.crypto_session.hasSessionKey()) {
            const err = new Error("NoSessionKey");
            err.name = "NoSessionKeyError";
            throw err;
        }
        const ct = (headers && headers["content-type"]) || "";
        if (ct.toLowerCase().startsWith("multipart/form-data")) {
            const err = new Error("Multipart uploads cannot be queued offline");
            err.name = "OfflineUploadError";
            throw err;
        }
        const blockedReason = _queueRequestBlockReason(url, method);
        if (blockedReason) {
            const err = new Error("Queue request is not allowlisted");
            err.name =
                blockedReason === "unsafe-method"
                    ? "UnsafeQueueMethodError"
                    : "UnsafeQueueUrlError";
            throw err;
        }
        // Refs #1466: Sequentielle WORKITEM_STATUS-POSTs derselben Aufgabe
        // (pk == URL) zu EINER Row zusammenfassen (Last-Write-Wins des Offline-
        // Intents). Beide Klicks tragen denselben eingefrorenen Token T0; mit
        // einem Token und einem Idempotenz-Key erzeugt der Replay keinen
        // Phantom-Selbstkonflikt mehr. Nur fuer Status-URLs (idempotenter
        // Ziel-Status); Edits/Anlagen bleiben eigenstaendige Rows.
        const patterns = (typeof self !== "undefined" && self.URL_PATTERNS) || null;
        if (
            patterns &&
            patterns.WORKITEM_STATUS &&
            patterns.matchesPath &&
            patterns.matchesPath(patterns.WORKITEM_STATUS, url)
        ) {
            const coalesced = await _store().coalescePendingQueueByUrl(url, method, body, headers);
            if (coalesced) {
                await _updateQueueCount();
                return;
            }
        }
        await _store().putEncrypted("queue", {
            url: url,
            createdAt: Date.now(),
            attempts: 0,
            retryAfter: 0,
            lastError: "",
            // Refs #1109 (F-09): Idempotenz-Schlüssel einmalig beim Enqueue
            // festlegen, damit er über alle Replay-Versuche stabil bleibt.
            // Refs #1351 (Bug #1): einen vom SW-Erstversuch durchgereichten
            // Key bevorzugen (geteilte Idempotenz ueber die SW→Queue-Grenze).
            idempotencyKey: _incomingIdempotencyKey(headers) || _newIdempotencyKey(),
            data: { method: method, body: body, headers: headers || {} },
        });
        await _updateQueueCount();
    }

    async function getQueueCount() {
        try {
            return await _store().count("queue");
        } catch (_e) {
            return 0;
        }
    }

    async function _isReady(record) {
        // Refs #1351/#1384: `conflict`/`dead` sind vom Auto-Replay
        // ausgeschlossen — beide sind ein permanentes Klassifikationsergebnis
        // (Versionskonflikt bzw. dauerhafter Fehler), kein transientes
        // Backoff-Fenster. Sie werden nur ueber eine explizite Nutzeraktion
        // (M8: retryQueueEntry/discardQueueEntry) wieder aktiv.
        if (record.localStatus === "conflict" || record.localStatus === "dead") return false;
        return !record.retryAfter || record.retryAfter <= Date.now();
    }

    function _backoffFor(attempts) {
        return Math.min(BASE_BACKOFF_MS * Math.pow(2, attempts), MAX_BACKOFF_MS);
    }

    // Refs #1351/#1384: true, wenn der urspruengliche (beim Enqueue
    // gespeicherte) Request einen HX-Request-Header trug — die Fetch-API
    // normalisiert Header-Keys beim Lesen auf lowercase (sw.js:148-156
    // schreibt sie entsprechend), ein case-insensitiver Check schuetzt
    // zusaetzlich gegen abweichend geschriebene Test-/Zukunfts-Records.
    function _hasHxRequestHeader(record) {
        const headers = (record.data && record.data.headers) || {};
        return Object.keys(headers).some((k) => k.toLowerCase() === "hx-request");
    }

    // Baut einen Replay-Request aus einem Queue-Record. Refs #1109 (F-09):
    // Idempotenz-Schlüssel bei jedem Replay-Versuch mitschicken, damit der
    // Server einen Wiederholungs-POST nach Verbindungsabbruch als solchen
    // erkennt.
    async function _send(record, csrf) {
        // Zweite Grenze unmittelbar vor dem Netz: Altbestand oder manipulierte
        // IndexedDB-Zeilen duerfen die Enqueue-Pruefung nie umgehen.
        const blockedReason = _queueRequestBlockReason(
            record.url,
            record.data && record.data.method
        );
        if (blockedReason) {
            const err = new Error("Queue request is not allowlisted");
            err.name = "UnsafeQueueRequestError";
            throw err;
        }
        const headers = Object.assign({}, record.data.headers);
        // Refs #1351: Der Service Worker reicht den Idempotency-Key
        // KLEINGESCHRIEBEN (``x-idempotency-key``) in die Record-Header durch
        // (sw.js-Allowlist). Wir setzen unten den kanonischen, grossgeschriebenen
        // ``X-Idempotency-Key``. Ohne dieses Entfernen traegt das an ``fetch``
        // uebergebene Objekt BEIDE Case-Varianten desselben Header-Namens, die
        // die Headers-API zu ``"KEY, KEY"`` zusammenfasst → der Server-Cache-Key
        // matcht den Erstversuch nicht mehr → Doppel-Anlage. Genau EINEN Key senden.
        for (const k of Object.keys(headers)) {
            if (k.toLowerCase() === "x-idempotency-key") delete headers[k];
        }
        // Refs #1351/#1384 (HTTP-Replay-Contract): der Replay ist immer ein
        // JSON-Client — ueberschreibt gezielt NUR diesen einen Header,
        // unabhaengig davon, welchen Accept-Header der urspruengliche
        // (evtl. aus einem normalen Formular-Submit stammende) Request beim
        // Enqueue trug. Alle anderen Record-Header-Werte bleiben unangetastet.
        headers["Accept"] = "application/json";
        // Refs #1351 (Bug #5): Jeder Replay traegt den Marker X-Offline-Replay,
        // damit der Service Worker A's EIGENEN Replay network-only durchreicht
        // (kein Re-Intercept/Re-Queue) — sonst faenge er ihn bei >6s erneut ab
        // (Doppelkanal + spurious dead-letter).
        headers["X-Offline-Replay"] = REPLAY_PROTOCOL_VERSION;
        // Refs #1419 (Bugfix, analog zum Idempotency-Key oben): die
        // SW-Allowlist friert den x-csrftoken des Erstversuchs
        // KLEINGESCHRIEBEN im Record ein. Zusammen mit dem frischen
        // kanonischen X-CSRFToken ergaebe das ZWEI Case-Varianten desselben
        // Headers, die die Headers-API zu "stale, fresh" zusammenfasst →
        // Djangos CSRF-Check lehnt ab → JEDER Replay eines HTMX-/fetch-POSTs
        // landete als 403-dead. Die Record-Variante nur entfernen, wenn ein
        // frischer Token vorliegt — sonst bleibt sie der letzte Fallback.
        if (csrf) {
            for (const k of Object.keys(headers)) {
                if (k.toLowerCase() === "x-csrftoken") delete headers[k];
            }
            headers["X-CSRFToken"] = csrf;
        }
        if (record.idempotencyKey) headers["X-Idempotency-Key"] = record.idempotencyKey;
        // Refs #2528: check immediately before each write, including retries.
        // Decrypted work may have been read before the absolute deadline.
        await window.crypto_session?.awaitReady?.();
        if (!window.crypto_session || !window.crypto_session.hasSessionKey()) {
            const err = new Error("NoSessionKey");
            err.name = "NoSessionKeyError";
            throw err;
        }
        // Read the shared decision immediately before every write, including retries.
        if (typeof window.offlineStore?.isRevocationPending !== "function" ||
            await window.offlineStore.isRevocationPending()) {
            const err = new Error("RevocationPending");
            err.name = "RevocationPendingError";
            throw err;
        }
        return fetch(record.url, {
            method: record.data.method,
            body: record.data.body,
            headers: headers,
            credentials: "same-origin",
        });
    }

    // Refs #1351/#1384: einen Queue-Record dauerhaft als "dead" markieren
    // (S1: KEIN Loeschen — bleibt bis zu einer expliziten Nutzeraktion via
    // M8-UI erhalten). `record` ist die bereits ENTSCHLUESSELTE Zeile aus
    // `listDecrypted`; `putEncrypted` re-verschluesselt `data` beim Schreiben.
    // `lastError`/`deadReason`/`lastAttemptAt` sind bei `queue` — anders als
    // bei `events` — unverschluesselte Row-Felder (Analog zu `localStatus`),
    // nicht Teil des Envelopes.
    async function _markQueueDead(record, reason, statusStr) {
        await _store().putEncrypted("queue", {
            ...record,
            attempts: (record.attempts || 0) + 1,
            lastError: statusStr,
            localStatus: "dead",
            deadReason: reason,
            lastAttemptAt: Date.now(),
        });
    }

    // Refs #2289: jede erfolgreich uebertragene Zeile beendet zugleich den
    // Hinweis „Anmeldung erforderlich" — es ist wieder belegt geschrieben
    // worden.
    async function _deleteQueueRowAsSuccess(record, confirmed = false) {
        _signalAuthPending(false);
        // Refs #2561: a confirmed replay acknowledges only its own snapshot.
        const payload = record.data;
        if (confirmed && payload && new URL(record.url, window.location.origin).pathname === "/events/new/") {
            const token = new URLSearchParams(payload.body).get("_autosave_token");
            if (token) {
                // Public offline shells have no user ID. The encrypted token
                // identifies the snapshot without adding account metadata.
                const drafts = await _store().listDecrypted("drafts");
                for (const draft of drafts) {
                    if (draft.data.__submissionToken === token && draft.formKey.endsWith("_/events/new/")) {
                        await _store().deleteConfirmedDraft(draft.formKey, token);
                    }
                }
            }
        }
        await _store().deleteRow("queue", record.id);
    }

    async function replayQueue() {
        if (!navigator.onLine) return;
        await window.crypto_session?.awaitReady?.();
        if (!window.crypto_session || !window.crypto_session.hasSessionKey()) return;

        const records = await _store().listDecrypted("queue");
        if (records.length === 0) return;

        let csrf = _csrfFromMeta();
        if (!csrf) csrf = await _refreshCsrf();

        for (const record of records) {
            if (!(await _isReady(record))) continue;
            const blockedReason = _queueRequestBlockReason(
                record.url,
                record.data && record.data.method
            );
            if (blockedReason) {
                // Keine URL-/Methoden-/Body-Werte in Klartext-Statusfeldern
                // ablegen.
                await _markQueueDead(record, blockedReason, "blocked");
                continue;
            }
            try {
                let response = await _send(record, csrf);
                // Refs #1332: analog #1330 — ein 403 kann an einem zur
                // Precache-Zeit eingefrorenen, veralteten CSRF-Meta (aus einer
                // SW-gecachten Shell) liegen, nicht an fehlendem Recht. Einmal
                // mit dem frisch vom dedizierten CSRF-Endpoint geholten Token
                // (Refs #1408, _refreshCsrf oben) nachfassen, bevor der Record
                // als 4xx liegen bleibt und die Queue anhaelt. Der frische
                // Token gilt auch fuer die restlichen Records.
                if (response.status === 403) {
                    const fresh = await _refreshCsrf();
                    if (fresh && fresh !== csrf) {
                        csrf = fresh;
                        response = await _send(record, csrf);
                    }
                }
                // Refs #1419 (P0-Safety-Net): Ein auth-pending-Login-Bounce
                // kann als 200 statt als Redirect eintreffen — HtmxSession-
                // Middleware wandelt fuer HTMX-Requests (der Replay eines
                // Status-Toggle-Records traegt den eingefrorenen
                // ``hx-request``-Header) den Login-302 in ``200 + HX-Redirect``
                // um. Ohne diesen Zweig faenge der ``ok && !redirected &&
                // hasHxRequest``-Erfolgspfad unten das ab und LOESCHTE die
                // Zeile — stiller Datenverlust (ADR-030 §3). Ein echter
                // Status-/Edit-Erfolg traegt NIE ``HX-Redirect`` (nur der
                // Login-Bounce tut das), daher ist der Waechter
                // false-positive-frei. Wie der Redirect-auth-pending-Zweig:
                // Schleife anhalten OHNE zu loeschen (Middleware reicht
                // Replays inzwischen den rohen 302 durch — Root-Cause-Fix —,
                // dieser Zweig ist das Netz gegen die stille Loesch-Klasse).
                // Refs #1419 / #2289: Auth-Klassifikation VOR jeder
                // Erfolgsdeutung — gefolgter Redirect, HX-Redirect (die
                // HtmxSession-Middleware wandelt den Auth-302 fuer
                // HTMX-Requests in ``200 + HX-Redirect``) und ungefolgtes 3xx,
                // je auf /login/, /mfa/, /password-change/, /password-reset/.
                // Zeile bleibt UNVERAENDERT (kein attempts++, kein
                // Statuswechsel) — ein stiller Verwurf waere Datenverlust —,
                // und die gesamte Schleife haelt an: jeder weitere Record
                // kassierte dieselbe Weiterleitung.
                const authKind = _authPendingResponseKind(response);
                if (authKind) {
                    _signalAuthPending(true, authKind);
                    break;
                }
                // Refs #2289: belastbare Serverbestaetigung = Erfolg. Sie
                // kommt von den Create-/Edit-Endpunkten (Ereignis, Aufgabe)
                // NACH einem festgeschriebenen Schreibvorgang.
                if (response.ok && _isSavedConfirmed(response)) {
                    await _deleteQueueRowAsSuccess(record, true);
                    continue;
                }
                // Refs #1351/#1384: Klassifikation exakt nach der
                // HTTP-Replay-Contract-Tabelle (Plan-Kopf) — kein
                // Head-of-Line-Blocking mehr (ein 4xx haelt nur DIESEN
                // Record an, `continue` zum naechsten Record statt `break`
                // der gesamten Schleife), ausser bei 429/5xx/auth-pending/
                // Netzfehler (die betreffen die gesamte Session/Verbindung).
                if (response.ok && response.redirected) {
                    // Erfolg (Redirect-Kontrakt) — Refs #2289 nur noch fuer die
                    // Queue-Ziele OHNE Markerunterstuetzung (Status-Wechsel,
                    // Strichliste, Audit-Notiz-Export). Jede Weiterleitung zur
                    // Anmeldung, zur Zwei-Faktor-Abfrage oder zum
                    // Passwortwechsel ist oben bereits als "ausstehende
                    // Authentifizierung" abgefangen (Zeile bleibt, Batch haelt
                    // an); die markertragenden Create-/Edit-Endpunkte kommen
                    // ebenfalls nicht hier an.
                    await _deleteQueueRowAsSuccess(record);
                } else if (response.ok && !response.redirected && _hasHxRequestHeader(record)) {
                    // Erfolg (HTMX-Partial-Kontrakt): ein 200 ohne Redirect
                    // ist fuer einen HX-Request-Submit der normale Erfolgsfall.
                    await _deleteQueueRowAsSuccess(record);
                } else if (response.ok && !response.redirected) {
                    // Nach B4 antworten alle bekannten Fehlerpfade mit
                    // 409/422 — ein nacktes 200 ausserhalb des HTMX-Kontrakts
                    // ist anomal. Nie still loeschen -> dead statt Erfolg.
                    await _markQueueDead(record, "unexpected-response", "" + response.status);
                    continue;
                } else if (response.status === 409) {
                    // Stage 3 (#575) — optimistic concurrency conflict. Do
                    // NOT retry (the stale token would bounce again); mark
                    // the queued record as conflict so the conflict-list
                    // UI can pick it up. The actual merge round-trip goes
                    // through offline-edit.js, but generic queue entries
                    // (e.g. from an offline CREATE-then-EDIT roll-up) may
                    // still hit this branch.
                    //
                    // Refs #1419 (zugleich Baustein (a) von #1390): den
                    // maschinenlesbaren Server-Stand aus dem 409-Body am
                    // Record persistieren — im verschluesselten data-Envelope,
                    // weil server_state Titel/Beschreibung traegt. Damit kann
                    // die M8-Liste den Konflikt fachlich rendern (Dein Status
                    // vs. Server-Stand) und per "Erneut anwenden" mit frischem
                    // Token aufloesen. Kein JSON-Body (aeltere Server,
                    // Proxy-Fehlerseite): Konflikt bleibt generisch renderbar.
                    let conflict = null;
                    try {
                        const body = await response.json();
                        conflict = { error: body.error || "conflict", serverState: body.server_state || null };
                    } catch (_jsonErr) {
                        /* kein/kaputter JSON-Body — generischer Konflikt */
                    }
                    await _store().putEncrypted("queue", {
                        ...record,
                        attempts: (record.attempts || 0) + 1,
                        lastError: "409",
                        localStatus: "conflict",
                        data: conflict ? { ...record.data, conflict: conflict } : record.data,
                    });
                    continue; // try the next queued record, don't halt
                } else if (response.status === 422 || response.status === 400) {
                    await _markQueueDead(record, "invalid", "" + response.status);
                    continue;
                } else if (response.status === 404 || response.status === 410) {
                    await _markQueueDead(record, "not-found", "" + response.status);
                    continue;
                } else if (response.status === 403) {
                    // Der CSRF-Refresh-Retry ist oben bereits gelaufen und
                    // hat immer noch 403 kassiert — kein Token-, sondern ein
                    // Rechteproblem. Via M8-UI manuell retry-bar.
                    await _markQueueDead(record, "forbidden", "403");
                    continue;
                } else if (response.status === 429) {
                    // Ratelimit ist user-global — Backoff setzen und die
                    // GESAMTE Schleife anhalten (nicht nur diesen Record).
                    const attempts = (record.attempts || 0) + 1;
                    await _store().putEncrypted("queue", {
                        ...record,
                        attempts: attempts,
                        retryAfter: Date.now() + _backoffFor(attempts),
                        lastError: "429",
                    });
                    break;
                } else if (response.status >= 500) {
                    // Server hiccup — exponential backoff, keep record
                    const attempts = (record.attempts || 0) + 1;
                    await _store().putEncrypted("queue", {
                        ...record,
                        attempts: attempts,
                        retryAfter: Date.now() + _backoffFor(attempts),
                        lastError: "" + response.status,
                    });
                    break;
                } else {
                    // Rest-Klasse unbekannter Antworten (per Contract-Tabelle
                    // nicht vorgesehen) — konservativ wie ein dauerhafter
                    // Fehler behandeln, damit kein Statuscode durchs Raster
                    // faellt und endlos retryt.
                    await _markQueueDead(record, "invalid", "" + response.status);
                    continue;
                }
            } catch (_e) {
                // Network gone again; stop and try later
                break;
            }
        }
        await _updateQueueCount();
    }

    window.addEventListener("online", () => {
        // M6 (Refs #1351/#1383): Der Sync-Orchestrator haelt den EINZIGEN
        // koordinierten online-Trigger (origin-weiter Web Lock, Multi-Tab). Ist
        // er geladen (base.html), uebernimmt seine requestSync-Sequenz diesen
        // Replay — hier nichts tun (sonst liefe der Replay doppelt/unkoordiniert).
        // Nur als Fallback auf Seiten OHNE Sync-Orchestrator direkt replayen wie bisher.
        if (window.syncOrchestrator && window.syncOrchestrator.requestSync) return;
        replayQueue();
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", _updateQueueCount);
    } else {
        _updateQueueCount();
    }

    // Refs #1351/#1385 (M8/Task 4): Cross-Tab-Refresh — ein Sync in Tab A
    // (BroadcastChannel liefert dessen eigenen Lauf nicht an sich selbst
    // zurueck) aktualisiert Tab B's Queue-Zaehler (Banner: pending/blocked)
    // ohne Polling.
    //
    // Refs #1409/#1828: base.html laedt sync-orchestrator.js NACH
    // offline-queue.js — direkt registrieren geht hier also (noch) nicht.
    // Statt des frueher pro Konsument duplizierten DOMContentLoaded/
    // naechster-Tick-Retry-Geruests wandert der Empfaenger in die
    // Pending-Liste, die sync-orchestrator.js beim Laden draint (und danach
    // als Sofort-Registrierung uebernimmt, siehe dort).
    function _onOrchestratorMessage(msg) {
        if (msg && msg.type === "sync-finished") _updateQueueCount();
    }
    if (window.syncOrchestrator && window.syncOrchestrator.onMessage) {
        window.syncOrchestrator.onMessage(_onOrchestratorMessage);
    } else {
        (window.syncOrchestratorPending = window.syncOrchestratorPending || []).push(
            _onOrchestratorMessage
        );
    }

    window.offlineQueue = {
        enqueueRequest: enqueueRequest,
        replayQueue: replayQueue,
        getQueueCount: getQueueCount,
        // Refs #1468: die Konfliktliste stoesst nach Verwerfen/Erneut den
        // Queue-Zaehler-Refresh an (offlineStore-Primitive feuern selbst nichts).
        refreshCount: _updateQueueCount,
        // Refs #1479 (TST-01): reine Export-Oberflaeche fuer den JS-Unit-Runner
        // (vitest) — kein Verhaltens-Refactor. Diese internen Helfer tragen die
        // sicherheitskritische Replay-Klassifikation (Backoff, CSRF-Refresh,
        // Ready-Gate) und werden isoliert getestet.
        _backoffFor: _backoffFor,
        _refreshCsrf: _refreshCsrf,
        _isReady: _isReady,
        // Refs #2289: Klassifikations-Praedikate des Erfolgsbestaetigungs-
        // Vertrags — tests/js/offline-replay-auth.test.mjs haelt sie
        // deckungsgleich mit den Fassungen in offline-edit.js.
        _isAuthPendingTarget: _isAuthPendingTarget,
        _isAuthPendingResponse: _isAuthPendingResponse,
        _authPendingKind: _authPendingKind,
        _authPendingResponseKind: _authPendingResponseKind,
        _isSavedConfirmed: _isSavedConfirmed,
    };
})();
