/*
 * Offline-Edit Orchestrator (Stage 3, Refs #575, #572).
 *
 * Bridges the offline-read-cache (Stage 2) and the generic offline-queue
 * (Stage 1): when the user edits an event while offline, this module writes
 * the edited state into IndexedDB under `localStatus: "modified"` plus the
 * `expected_updated_at` token from the cached copy. When the network returns
 * we replay the edit against `/events/<pk>/edit/` with an
 * `Accept: application/json` header; the server honours that with either a
 * redirect (success) or a 409 JSON body (conflict) that we stash back into
 * IndexedDB under `localStatus: "conflict"` for the merge UI to pick up.
 *
 * This module deliberately does NOT use the generic queue — offline edits
 * must round-trip through a dedicated replay because they need to read the
 * 409 response body and carry per-event status information, both of which
 * the queue's fire-and-forget model does not provide.
 */
(function () {
    "use strict";

    function _store() {
        if (!window.offlineStore) {
            throw new Error("OfflineStoreNotLoaded");
        }
        return window.offlineStore;
    }

    function _csrfFromMeta() {
        // Refs #602: CSRF_COOKIE_HTTPONLY verbietet JS-Zugriff aufs Cookie,
        // Token kommt aus dem <meta name="csrf-token">-Tag im Basistemplate.
        if (typeof window.getCsrfToken === "function") {
            return window.getCsrfToken() || null;
        }
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute("content") || null : null;
    }

    async function _refreshCsrf() {
        try {
            const resp = await fetch("/login/", {
                method: "GET",
                credentials: "same-origin",
            });
            if (resp.ok) {
                const html = await resp.text();
                const m = html.match(/name=["']csrf-token["']\s+content=["']([^"']+)["']/i);
                if (m) return m[1];
            }
        } catch (_e) {
            // Still offline, ignore.
        }
        return _csrfFromMeta();
    }

    function _postForm(url, body, csrf, extraHeaders) {
        const headers = Object.assign(
            {
                "Content-Type": "application/x-www-form-urlencoded",
                Accept: "application/json",
                // Refs #1351 (Bug #5): Alle Offline-Edit-Replays (Create UND
                // Edit gehen ueber _postForm) tragen den Marker X-Offline-Replay.
                // Der Service Worker reicht markierte Requests network-only durch
                // (kein Re-Intercept/Re-Queue) — sonst faenge er A's eigenen
                // Replay bei >6s erneut ab (Doppelkanal + spurious dead-letter).
                "X-Offline-Replay": "1",
            },
            extraHeaders || {}
        );
        if (csrf) headers["X-CSRFToken"] = csrf;
        return fetch(url, {
            method: "POST",
            body: body,
            headers: headers,
            credentials: "same-origin",
        });
    }

    // Refs #1330: Die aus dem SW-Cache servierte In-Place-Shell (/clients/<pk>/,
    // Refs #1322) kann ein zur Precache-Zeit eingefrorenes, veraltetes
    // <meta name="csrf-token"> tragen (der Login rotiert den Token danach). Ein
    // 403 ist dann KEIN Rechteentzug, sondern ein reines Token-Problem — einmal
    // mit frisch von /login/ geholtem Token nachfassen, bevor der Replay als
    // "revoked" gilt. Gibt null zurück, wenn das Netz wegbricht.
    async function _postFormWithCsrfRetry(url, body, csrf, extraHeaders) {
        let response;
        try {
            response = await _postForm(url, body, csrf, extraHeaders);
        } catch (_e) {
            return null;
        }
        if (response.status === 403) {
            const fresh = await _refreshCsrf();
            // `fresh !== csrf` filtert KEINE echten Rechteentzug-403 heraus:
            // Django re-maskt den Token bei jedem Render, `fresh` unterscheidet
            // sich also praktisch immer vom Ausgangs-`csrf`. Der Guard unterdrückt
            // den Retry nur, wenn `_refreshCsrf` mangels Netz auf dasselbe (stale)
            // Meta zurückfiel — ein echter 403 wird nach einem verworfenen Retry
            // korrekt zu "revoked".
            if (fresh && fresh !== csrf) {
                try {
                    response = await _postForm(url, body, fresh, extraHeaders);
                } catch (_e) {
                    return null;
                }
            }
        }
        return response;
    }

    function _eventEditUrl(eventPk) {
        return "/events/" + encodeURIComponent(eventPk) + "/edit/";
    }

    function _fireCountEvent() {
        // Let the Alpine banner in base.html react without polling.
        _store()
            .countUnsyncedEvents()
            .then((count) => {
                window.dispatchEvent(
                    new CustomEvent("offline-unsynced-count", { detail: { count: count } })
                );
            })
            .catch(() => {
                /* ignore — banner just stays at its last known count */
            });
        // Refs #1351/#1385 (M8/Task 4): Konflikt-Banner-Zaehler = conflict+dead
        // (beide sind "wartet auf eine Nutzerentscheidung" — nur der
        // Aufloesungsweg unterscheidet sich: Resolver vs. Retry/Verwerfen).
        Promise.all([_store().countConflictEvents(), _store().countDeadEvents()])
            .then(([conflictCount, deadCount]) => {
                window.dispatchEvent(
                    new CustomEvent("offline-conflict-count", { detail: { count: conflictCount + deadCount } })
                );
            })
            .catch(() => {});
    }

    /*
     * Read the optimistic-lock token (`updated_at`) of the cached event.
     *
     * Refs #1109 (F-07): Das Offline-Bundle serialisiert pro Event jetzt
     * ein `updated_at` (`offline.py:_serialize_event`). Liegt im IndexedDB
     * noch der CLEAN-Record aus dem Bundle, trägt dessen Envelope dieses
     * `updated_at`. Wir lesen es hier, bevor `markEventModified` den Record
     * mit dem Edit-Envelope überschreibt — sonst ginge der Token verloren und
     * der Replay würde mit leerem `expected_updated_at` rausgehen (silent LWW).
     */
    async function _cachedUpdatedAt(eventPk) {
        if (window.crypto_session && window.crypto_session.ready) {
            await window.crypto_session.ready();
        }
        if (!window.crypto_session || !window.crypto_session.hasSessionKey()) {
            // Refs #1351/#1384: ein transienter Key-Fehler (Idle-Lock #1324)
            // darf NICHT zu "" degradieren — ein leerer Token wuerde den
            // Server-Konflikt-Check umgehen (fehlender Token ist im
            // HTTP-Replay-Contract ein eigener 409-"missing-token"-Fall) und
            // einen KONFLIKT vortaeuschen, wo in Wahrheit nur der Schluessel
            // gerade gesperrt ist. Aufrufer muessen diesen Fehler abfangen
            // und den Replay dieses Events ueberspringen (kein tokenloser
            // POST) statt mit leerem Token weiterzumachen.
            const err = new Error("NoSessionKey");
            err.name = "NoSessionKeyError";
            throw err;
        }
        const cached = await _store().getOfflineEvent(String(eventPk));
        if (!cached) return "";
        // Refs #1351/#1384: `cached` ist die Store-ROW ({pk, clientPk, ...,
        // data}) — das Envelope mit `updated_at`/`expectedUpdatedAt` liegt in
        // `cached.data`, NICHT auf `cached` selbst. Der vorherige Code las
        // `cached.updated_at`/`cached.expectedUpdatedAt` (stets `undefined`)
        // und lieferte dadurch IMMER "" — der Fallback war seit F-07/#1109
        // faktisch nie funktionsfaehig.
        const envelope = cached.data || {};
        // CLEAN-Bundle-Records tragen `updated_at` direkt im Envelope;
        // bereits modifizierte Records tragen `expectedUpdatedAt`.
        return envelope.updated_at || envelope.expectedUpdatedAt || "";
    }

    /*
     * Persist a modified event in IndexedDB.
     *
     * `formData` is the flat object of slug → value that the edit form
     * produces (plus `expected_updated_at` either from the cached record
     * or from a prior conflict resolution). The record lands in the
     * `events` table where `getOfflineClient` already reads from, so the
     * read-cache immediately reflects the pending change.
     */
    async function markEventModified(eventPk, formData, options) {
        const opts = options || {};
        const pk = String(eventPk);
        // Refs #1109 (F-07): Fehlt der Token explizit, aus dem gecachten
        // Bundle-Event nachziehen, damit der Replay den Server-Konflikt-Check
        // scharf stellt statt bedingungslos zu überschreiben.
        let token = opts.expectedUpdatedAt || "";
        if (!token) {
            token = await _cachedUpdatedAt(eventPk);
        }
        // Refs #1351: bestehende Row lesen — ein offline NEU angelegtes, noch
        // nie synchronisiertes Event (localStatus "new") darf durch einen
        // Re-Edit NICHT zu "modified" degradieren. saveOfflineEdit ersetzt die
        // Row vollstaendig (kein Merge); ohne diesen Erhalt wuerde jeder
        // weitere Replay-Versuch dauerhaft auf die serverseitig nie
        // existierende /events/<pk>/edit/-URL zielen statt auf /events/new/ —
        // das Event wuerde nie angelegt (verletzt die S1-Invariante „unsynced
        // stirbt nie still").
        const existing = await _store().getOfflineEvent(pk);
        const wasNew = Boolean(existing && existing.localStatus === "new");
        const data = {
            formData: formData,
            expectedUpdatedAt: token,
            // Snapshot a few metadata fields so the list/review UI can
            // label the edit without cross-referencing the cache. Refs
            // #1111: ``documentTypePk`` + ``occurredAt`` let the offline
            // viewer re-render and re-edit a still-unsynced event (the
            // clean bundle record is overwritten by this edit envelope).
            documentTypeName: opts.documentTypeName || "",
            documentTypePk: opts.documentTypePk || "",
            occurredAt: opts.occurredAt || "",
            // Refs #1111: bestehende Datei-Anhang-Marker (nicht editierbar)
            // fuer die Offline-Anzeige bewahren — werden in
            // normalizeOfflineEventRecord wieder in data_fields gemerged,
            // gehen NICHT in formData/Replay ein.
            fileMarkers: opts.fileMarkers || {},
            lastEditedAt: Date.now(),
        };
        if (wasNew) {
            // Refs #1351: der Idempotenzschutz der urspruenglichen Neuanlage
            // (F-09, #1109) muss ueber den Re-Edit hinweg erhalten bleiben —
            // sonst schickt der naechste Replay ein ANDERES Idempotency-Key
            // mit und der Doppel-Anlage-Schutz fuer den ORIGINAL-Request
            // greift nicht mehr.
            data.idempotencyKey = (existing.data && existing.data.idempotencyKey) || _uuid();
        }
        const record = {
            pk: pk,
            clientPk: opts.clientPk || "",
            occurredAt: opts.occurredAt || "",
            localStatus: wasNew ? "new" : opts.localStatus || "modified",
            data: data,
        };
        await _store().saveOfflineEdit(record);
        _fireCountEvent();
        return record;
    }

    function _uuid() {
        if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
        // Fallback (im Secure Context — Voraussetzung fuer Offline — nie noetig).
        return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
            const r = (Math.random() * 16) | 0;
            return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
        });
    }

    /*
     * Refs #1323: Ein offline NEU angelegtes Ereignis in IndexedDB ablegen.
     * localStatus "new" mit client-generierter pk; beim Reconnect spielt
     * replayModifiedEvent es gegen ``/events/new/`` (statt ``/edit/``). Der
     * Idempotenz-Key schuetzt gegen Doppel-Anlage, wenn die Verbindung nach
     * dem Server-Write, aber vor der Response abbricht (F-09, #1109).
     */
    async function markEventNew(clientPk, documentTypePk, formData, options) {
        const opts = options || {};
        const record = {
            pk: opts.pk || _uuid(),
            clientPk: clientPk || "",
            occurredAt: opts.occurredAt || "",
            localStatus: "new",
            data: {
                formData: formData,
                documentTypePk: documentTypePk || "",
                documentTypeName: opts.documentTypeName || "",
                // Refs #1397: optionale Fall-Zuordnung offline erfassen; der
                // Replay sendet sie als ``case`` an /events/new/ (wie online).
                casePk: opts.casePk || "",
                occurredAt: opts.occurredAt || "",
                idempotencyKey: opts.idempotencyKey || _uuid(),
                lastEditedAt: Date.now(),
            },
        };
        // Refs #1356: Offline neu angelegte Eintraege existieren serverseitig
        // noch gar nicht — das schuetzenswerteste Gut. Fire-and-forget um
        // dauerhaften Speicher bitten (Eviction-Schutz), OHNE das Anlegen
        // durch eine Verweigerung/einen Fehler zu blockieren; kein
        // UI-Feedback an dieser Stelle.
        window.offlineStore.ensurePersistentStorage().catch(function () {});
        await _store().saveOfflineEdit(record);
        _fireCountEvent();
        return record;
    }

    /*
     * Replay eines offline neu angelegten Events gegen ``/events/new/``.
     * Erfolg = Redirect auf die Event-Detailseite. Refs #1351/#1384: nach M11
     * antwortet die Create-View auf ein ungueltiges Formular (roher
     * ``Accept: application/json``) mit 422+errors — der bestehende
     * 200-Re-Render-Fallback (aeltere/HX-Faelle) bleibt zusaetzlich erhalten.
     * Bei Erfolg den lokalen "new"-Record entfernen — die naechste
     * Re-Validierung holt das kanonische Server-Event mit seiner echten pk
     * ins Bundle.
     */
    async function replayNewEvent(record, csrf) {
        const data = record.data || {};
        const fd = data.formData || {};
        const form = new URLSearchParams();
        if (record.clientPk) form.set("client", record.clientPk);
        if (data.documentTypePk) form.set("document_type", data.documentTypePk);
        // Refs #1397: Fall-Zuordnung mitschicken (Feldname wie EventMetaForm).
        if (data.casePk) form.set("case", data.casePk);
        if (data.occurredAt) form.set("occurred_at", data.occurredAt);
        for (const [key, value] of Object.entries(fd)) {
            if (value === null || value === undefined) continue;
            if (Array.isArray(value)) {
                for (const item of value) {
                    if (item === null || item === undefined) continue;
                    form.append(key, String(item));
                }
            } else {
                form.append(key, String(value));
            }
        }
        const response = await _postFormWithCsrfRetry("/events/new/", form.toString(), csrf, {
            "X-Idempotency-Key": data.idempotencyKey || record.pk,
        });
        if (!response) {
            return { status: "network-error" };
        }
        // Refs #1351 (Bug #2): Ein Redirect auf /login/ ist KEIN Erfolg —
        // die Session ist waehrend des Offline-Fensters abgelaufen und fetch
        // folgt dem 302 transparent bis zur Login-Seite. Ohne diesen Guard
        // wuerde der nie serverseitig angelegte Record hier als "synced"
        // geloescht (stiller Datenverlust). Record UNVERAENDERT lassen, kein
        // clearOfflineEdit; der Aufrufer (replayAllModifiedEvents) bricht den
        // Batch ab (symmetrisch zur generischen Queue, HTTP-Replay-Contract).
        if (response.redirected && response.url.includes("/login/")) {
            return { status: "auth-pending" };
        }
        if (response.redirected || response.status === 302) {
            await _store().clearOfflineEdit(record.pk);
            _fireCountEvent();
            return { status: "synced" };
        }
        if (response.status === 403 || response.status === 404) {
            return { status: "revoked", statusCode: response.status };
        }
        if (response.status === 429) {
            // Refs #1351/#1384: Ratelimit ist user-global (analog
            // offline-queue.js) — Record unangetastet lassen (bleibt "new"),
            // der Aufrufer (replayAllModifiedEvents) bricht den Rest des
            // Batches ab statt das Ratelimit-Budget weiter zu verbrennen.
            return { status: "ratelimited", statusCode: response.status };
        }
        if (response.status === 422) {
            // Refs #1351/#1384 (M11): serverseitige Formularvalidierung
            // fehlgeschlagen, jetzt mit Feldfehlern statt eines blossen
            // Re-Renders. Record NICHT verwerfen — als "new" behalten, dem
            // Nutzer die Feldfehler melden (analog replayModifiedEvent/422).
            let body = null;
            try {
                body = await response.json();
            } catch (_e) {
                body = {};
            }
            return { status: "invalid", errors: (body && body.errors) || {} };
        }
        if (response.status === 200) {
            // Serverseitige Formularvalidierung fehlgeschlagen (Re-Render).
            // Record NICHT verwerfen — als "new" behalten, dem Nutzer melden.
            // Bestehender Fallback fuer den Fall, dass (noch) kein 422
            // geliefert wird (aeltere Server-Version / HX-Submit-Pfad).
            return { status: "invalid", errors: {} };
        }
        return { status: "error", statusCode: response.status };
    }

    async function replayModifiedEvent(record) {
        if (!navigator.onLine) return { status: "offline" };
        if (window.crypto_session && window.crypto_session.ready) {
            await window.crypto_session.ready();
        }
        if (!window.crypto_session || !window.crypto_session.hasSessionKey()) {
            return { status: "no-key" };
        }

        let csrf = _csrfFromMeta();
        if (!csrf) csrf = await _refreshCsrf();

        // Refs #1323: offline NEU angelegte Events gehen an /events/new/ statt
        // an den /edit/-Pfad (dessen pk serverseitig gar nicht existiert).
        if (record.localStatus === "new") {
            return await replayNewEvent(record, csrf);
        }

        const form = new URLSearchParams();
        const fd = (record.data && record.data.formData) || {};
        for (const [key, value] of Object.entries(fd)) {
            if (value === null || value === undefined) continue;
            // Refs #1111: MULTI_SELECT-Felder tragen ein Array — jeden Wert als
            // eigenen Key anhängen, damit Djangos MultipleChoiceField
            // (``value_from_datadict`` → ``getlist``) sie als Liste sieht statt
            // als einen kommaverbundenen String (= „ungültige Auswahl").
            if (Array.isArray(value)) {
                for (const item of value) {
                    if (item === null || item === undefined) continue;
                    form.append(key, String(item));
                }
            } else {
                form.append(key, String(value));
            }
        }
        // Refs #1109 (F-07): Token mitschicken, damit der Server-Konflikt-Check
        // greift. Fehlt er am Record (z.B. ein vor diesem Fix angelegter Edit),
        // einmalig aus dem gecachten Bundle-Event nachladen.
        let token = (record.data && record.data.expectedUpdatedAt) || "";
        if (!token) {
            try {
                token = await _cachedUpdatedAt(record.pk);
            } catch (e) {
                if (e && e.name === "NoSessionKeyError") {
                    // Refs #1351/#1384: transienter Key-Fehler (Idle-Lock) —
                    // dieses Event ueberspringen statt tokenlos zu POSTen
                    // (wuerde serverseitig "missing-token" ausloesen und
                    // einen Konflikt vortaeuschen, wo keiner existiert).
                    return { status: "locked" };
                }
                throw e;
            }
        }
        if (token) {
            form.set("expected_updated_at", token);
        }

        const response = await _postFormWithCsrfRetry(_eventEditUrl(record.pk), form.toString(), csrf);
        if (!response) {
            return { status: "network-error" };
        }

        // Refs #1351 (Bug #2): symmetrisch zu replayNewEvent — ein Redirect auf
        // /login/ ist KEIN Erfolg, sondern eine waehrend des Offline-Fensters
        // abgelaufene Session (fetch folgt dem 302 transparent bis zur
        // Login-Seite). Ohne diesen Guard wuerde der Erfolgszweig unten den
        // nie serverseitig gespeicherten Edit auf "clean" setzen (raus aus dem
        // Unsynced-Set = stiller Datenverlust bei blossem Session-Ablauf).
        // Record UNVERAENDERT lassen; der Aufrufer (replayAllModifiedEvents)
        // bricht den Batch ab (HTTP-Replay-Contract, constraints.md Z.29).
        if (response.redirected && response.url.includes("/login/")) {
            return { status: "auth-pending" };
        }

        // Erfolg ist NUR ein echtes Speichern: der Server bestaetigt mit einem
        // Redirect (302), dem fetch folgt -> response.redirected. Ein blankes 200
        // ist KEIN Erfolg — Refs #1111: ein ungueltiges Formular kam frueher als
        // 200 (re-rendertes Formular) zurueck und wurde faelschlich als "synced"
        // verworfen (stiller Datenverlust). Der Server liefert dafuer jetzt 422.
        if (response.redirected || response.status === 302) {
            // F-08 (#1111): synchronisiertes Event SICHTBAR lassen statt loeschen —
            // sonst verschwindet es aus der Offline-Ansicht, wenn ein
            // Geschwister-Edit desselben Klienten noch offen ist (revalidate
            // ueberspringt dann). Auf "clean" markieren: raus aus dem Unsynced-Set,
            // kein Re-Replay, weiter gerendert. Der naechste volle Re-Sync ersetzt
            // es durch das kanonische Server-Event.
            await _store().updateEventLocalStatus(record.pk, "clean");
            _fireCountEvent();
            return { status: "synced" };
        }
        if (response.status === 409) {
            // Refs #1351/#1385 (M8/Task 4): der Server liefert 409 in ZWEI
            // Varianten — `error:"conflict"` (Versionskonflikt) UND
            // `error:"missing-token"` (JSON-Edit ohne `expected_updated_at`,
            // Strang B). Beide tragen ein voll befuelltes `server_state`; wir
            // behandeln sie hier bewusst GLEICH (missing-token -> conflict-Flow):
            // die Verzweigung haengt nur am Status 409, nicht an `body.error`,
            // sodass der Konflikt-Resolver in beiden Faellen den Server-Stand
            // feldweise gegenueberstellt statt den Edit still zu verwerfen.
            let body = null;
            try {
                body = await response.json();
            } catch (_e) {
                body = { error: "conflict", server_state: {} };
            }
            await enqueueConflictForReview(record, body.server_state || {});
            return { status: "conflict", serverState: body.server_state };
        }
        if (response.status === 422) {
            // Server-seitige Formularvalidierung fehlgeschlagen (#1111): Edit NICHT
            // verwerfen, als "modified" behalten und dem Nutzer zur Korrektur
            // melden — sonst ginge die dokumentierte Aenderung still verloren.
            let body = null;
            try {
                body = await response.json();
            } catch (_e) {
                body = {};
            }
            await _store().updateEventLocalStatus(record.pk, "modified");
            _fireCountEvent();
            return { status: "invalid", errors: (body && body.errors) || {} };
        }
        if (response.status === 404 || response.status === 410) {
            // Refs #1351/#1384: das Edit-Ziel existiert serverseitig
            // dauerhaft nicht mehr (geloescht/nie existent) — anders als 403
            // (kann Rate-Limit-/Proxy-Rauschen sein) ist das PERMANENT.
            // "dead" statt endlosem "revoked"-Retry bei jedem weiteren
            // Reconnect; der nachgelagerte revalidateCachedClient purged den
            // Klienten weiterhin separat, falls der Zugriff selbst entzogen
            // wurde (F-10/#1110).
            await _store().markEventDead(record.pk, "not-found", "" + response.status);
            _fireCountEvent();
            return { status: "dead", deadReason: "not-found", statusCode: response.status };
        }
        if (response.status === 403) {
            // Der CSRF-Refresh-Retry ist in _postFormWithCsrfRetry bereits
            // gelaufen. Ein 403 ist seit #1354 KEIN Purge-Trigger mehr — er
            // kann Rate-Limit-/CSRF-/Proxy-Rauschen sein; bei echtem
            // Rechteentzug vernichtet die Salt-Rotation den Bestand
            // (permanenter Decrypt-Fehler, #1352). Der Edit bleibt lokal
            // erhalten (unveraendert ggue. dem bisherigen Verhalten).
            return { status: "revoked", statusCode: response.status };
        }
        if (response.status === 429) {
            // Refs #1351/#1384: Ratelimit ist user-global (analog
            // offline-queue.js) — Record unangetastet lassen (kein eigenes
            // Backoff-Feld im events-Schema noetig, der naechste
            // online-Kontakt versucht erneut), aber den Aufrufer
            // (replayAllModifiedEvents) den Rest des Batches abbrechen
            // lassen statt das Ratelimit-Budget weiter zu verbrennen.
            return { status: "ratelimited", statusCode: response.status };
        }
        // Transiente Fehler (5xx): Record behalten, spaeter erneut versuchen.
        return { status: "error", statusCode: response.status };
    }

    async function enqueueConflictForReview(record, serverState) {
        await _store().saveConflictState(record.pk, serverState || {});
        _fireCountEvent();
    }

    async function replayAllModifiedEvents() {
        // Refs #1352: Key-Gate VOR dem Listing — ohne Schluessel liest
        // listModifiedEvents() jede modifizierte Row als transienten
        // NoSessionKeyError und liefert (korrekt) eine leere Liste; das
        // wuerde diesen Lauf aber wie "nichts zu synchronisieren" aussehen
        // lassen, obwohl in Wahrheit nur der Schluessel fehlt (Idle-Lock
        // #1324). Sofort return statt eines fuer den Nutzer unsichtbaren No-Ops.
        if (window.crypto_session && window.crypto_session.ready) {
            await window.crypto_session.ready();
        }
        if (!window.crypto_session || !window.crypto_session.hasSessionKey()) {
            return;
        }
        // Best-effort loop: stop on the first network error so we don't
        // spam the server with failing requests, but carry on past a
        // conflict — conflicts are a per-record state, not a session-wide
        // halt. Refs #1351/#1384: "ratelimited" bricht den Batch ebenfalls ab
        // (Ratelimit ist user-global, nicht per-Record) — "locked" (transient
        // fehlender Schluessel bei EINEM Event) dagegen NICHT: die Schleife
        // laeuft mit den restlichen Events weiter. Refs #1351 (Bug #2):
        // "auth-pending" (Session abgelaufen, 302→/login/) bricht ebenfalls ab
        // — jeder weitere Record kassierte denselben Login-Redirect; die Rows
        // bleiben unveraendert liegen (kein still verworfener Edit), exakt wie
        // beim Netzfehler (symmetrisch zur generischen Queue).
        const records = await _store().listModifiedEvents();
        for (const record of records) {
            const result = await replayModifiedEvent(record);
            if (
                result.status === "network-error" ||
                result.status === "offline" ||
                result.status === "ratelimited" ||
                result.status === "auth-pending"
            ) {
                break;
            }
        }
    }

    // When the browser comes back online, drain the offline-edit queue
    // alongside the generic offline-queue. Independent from
    // offline-queue.js so that an edit never gets silently retried by the
    // generic queue (which cannot handle the 409 body).
    window.addEventListener("online", () => {
        // M6 (Refs #1351/#1383): siehe sync-orchestrator.js — der Orchestrator
        // haelt den einzigen koordinierten Replay-Trigger (origin-weiter Lock,
        // Multi-Tab). Fallback ohne Orchestrator: direkt replayen wie bisher.
        if (window.syncOrchestrator && window.syncOrchestrator.requestSync) return;
        replayAllModifiedEvents();
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", _fireCountEvent);
    } else {
        _fireCountEvent();
    }

    // Refs #1351/#1385 (M8/Task 4): Cross-Tab-Refresh — synct Tab A in Tab B
    // (BroadcastChannel liefert den eigenen Sync-Lauf nicht an den Sender
    // selbst zurueck; die window-CustomEvents oben feuern nur lokal pro Tab),
    // aktualisiert Tab B's unsynced-/conflict-Zaehler (Banner, Detail-View)
    // ohne Polling.
    if (window.syncOrchestrator && window.syncOrchestrator.onMessage) {
        window.syncOrchestrator.onMessage((msg) => {
            if (msg && msg.type === "sync-finished") _fireCountEvent();
        });
    }

    window.offlineEdit = {
        markEventModified: markEventModified,
        markEventNew: markEventNew,
        replayModifiedEvent: replayModifiedEvent,
        replayAllModifiedEvents: replayAllModifiedEvents,
        enqueueConflictForReview: enqueueConflictForReview,
        refreshCounts: _fireCountEvent,
    };
})();
