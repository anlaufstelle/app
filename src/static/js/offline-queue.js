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
 *     still sent); 403 -> one CSRF-refresh retry, then dead; a Login-
 *     redirect mid-batch (session expired) halts the loop WITHOUT deleting
 *     the record ("auth-pending"); an unexpected bare 200 also becomes dead
 *     instead of being silently treated as success.
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

    async function enqueueRequest(url, method, body, headers) {
        if (window.crypto_session && window.crypto_session.ready) {
            await window.crypto_session.ready();
        }
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
    function _send(record, csrf) {
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
        headers["X-Offline-Replay"] = "1";
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

    async function replayQueue() {
        if (!navigator.onLine) return;
        if (window.crypto_session && window.crypto_session.ready) {
            await window.crypto_session.ready();
        }
        if (!window.crypto_session || !window.crypto_session.hasSessionKey()) return;

        const records = await _store().listDecrypted("queue");
        if (records.length === 0) return;

        let csrf = _csrfFromMeta();
        if (!csrf) csrf = await _refreshCsrf();

        for (const record of records) {
            if (!(await _isReady(record))) continue;
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
                const hxRedirect = response.headers.get("HX-Redirect");
                if (response.ok && hxRedirect && hxRedirect.includes("/login/")) {
                    break;
                }
                // Refs #1351/#1384: Klassifikation exakt nach der
                // HTTP-Replay-Contract-Tabelle (Plan-Kopf) — kein
                // Head-of-Line-Blocking mehr (ein 4xx haelt nur DIESEN
                // Record an, `continue` zum naechsten Record statt `break`
                // der gesamten Schleife), ausser bei 429/5xx/auth-pending/
                // Netzfehler (die betreffen die gesamte Session/Verbindung).
                if (response.ok && response.redirected && !response.url.includes("/login/")) {
                    // Erfolg (Redirect-Kontrakt).
                    await _store().deleteRow("queue", record.id);
                } else if (response.ok && response.redirected && response.url.includes("/login/")) {
                    // auth-pending: Session waehrend des Batches abgelaufen.
                    // Row bleibt UNVERAENDERT (kein attempts++, kein
                    // Statuswechsel) — ein stiller Verwurf waere ein
                    // Datenverlust. Die gesamte Schleife haelt an: jeder
                    // weitere Record wuerde denselben Login-Redirect kassieren.
                    break;
                } else if (response.ok && !response.redirected && _hasHxRequestHeader(record)) {
                    // Erfolg (HTMX-Partial-Kontrakt): ein 200 ohne Redirect
                    // ist fuer einen HX-Request-Submit der normale Erfolgsfall.
                    await _store().deleteRow("queue", record.id);
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
        // Nur als Fallback auf Seiten OHNE Orchestrator direkt replayen wie bisher.
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
    // Refs #1409: base.html laedt sync-orchestrator.js NACH offline-queue.js
    // (Z. 130 vor Z. 134) — ein einmaliges Parse-Zeit-Gate wie zuvor hier ist
    // IMMER false (window.syncOrchestrator existiert noch nicht) und der
    // Handler wird nie registriert (toter Code). Lade-reihenfolge-tolerant
    // wie crypto.js:402-418: jetzt versuchen, sonst auf
    // DOMContentLoaded/naechsten Tick nachholen.
    var _orchestratorReceiverRegistered = false;
    function _registerOrchestratorReceiver() {
        if (_orchestratorReceiverRegistered) return true;
        if (window.syncOrchestrator && window.syncOrchestrator.onMessage) {
            window.syncOrchestrator.onMessage((msg) => {
                if (msg && msg.type === "sync-finished") _updateQueueCount();
            });
            _orchestratorReceiverRegistered = true;
            return true;
        }
        return false;
    }
    if (!_registerOrchestratorReceiver()) {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", _registerOrchestratorReceiver);
        } else {
            window.setTimeout(_registerOrchestratorReceiver, 0);
        }
    }

    window.offlineQueue = {
        enqueueRequest: enqueueRequest,
        replayQueue: replayQueue,
        getQueueCount: getQueueCount,
    };
})();
