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
        _store()
            .countConflictEvents()
            .then((count) => {
                window.dispatchEvent(
                    new CustomEvent("offline-conflict-count", { detail: { count: count } })
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
        try {
            const cached = await _store().getOfflineEvent(String(eventPk));
            if (!cached) return "";
            // CLEAN-Bundle-Records tragen `updated_at` direkt im Envelope;
            // bereits modifizierte Records tragen `expectedUpdatedAt`.
            return cached.updated_at || cached.expectedUpdatedAt || "";
        } catch (_e) {
            return "";
        }
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
        // Refs #1109 (F-07): Fehlt der Token explizit, aus dem gecachten
        // Bundle-Event nachziehen, damit der Replay den Server-Konflikt-Check
        // scharf stellt statt bedingungslos zu überschreiben.
        let token = opts.expectedUpdatedAt || "";
        if (!token) {
            token = await _cachedUpdatedAt(eventPk);
        }
        const record = {
            pk: String(eventPk),
            clientPk: opts.clientPk || "",
            occurredAt: opts.occurredAt || "",
            localStatus: opts.localStatus || "modified",
            data: {
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
            },
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
                occurredAt: opts.occurredAt || "",
                idempotencyKey: opts.idempotencyKey || _uuid(),
                lastEditedAt: Date.now(),
            },
        };
        await _store().saveOfflineEdit(record);
        _fireCountEvent();
        return record;
    }

    /*
     * Replay eines offline neu angelegten Events gegen ``/events/new/``.
     * Erfolg = Redirect auf die Event-Detailseite (der Create-View re-rendert
     * bei invaliden Formularen mit 200, KEIN 422 wie der Edit-View). Bei Erfolg
     * den lokalen "new"-Record entfernen — die naechste Re-Validierung holt das
     * kanonische Server-Event mit seiner echten pk ins Bundle.
     */
    async function replayNewEvent(record, csrf) {
        const data = record.data || {};
        const fd = data.formData || {};
        const form = new URLSearchParams();
        if (record.clientPk) form.set("client", record.clientPk);
        if (data.documentTypePk) form.set("document_type", data.documentTypePk);
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
        if (response.redirected || response.status === 302) {
            await _store().clearOfflineEdit(record.pk);
            _fireCountEvent();
            return { status: "synced" };
        }
        if (response.status === 403 || response.status === 404) {
            return { status: "revoked", statusCode: response.status };
        }
        if (response.status === 200) {
            // Serverseitige Formularvalidierung fehlgeschlagen (Re-Render).
            // Record NICHT verwerfen — als "new" behalten, dem Nutzer melden.
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
            token = await _cachedUpdatedAt(record.pk);
        }
        if (token) {
            form.set("expected_updated_at", token);
        }

        const response = await _postFormWithCsrfRetry(_eventEditUrl(record.pk), form.toString(), csrf);
        if (!response) {
            return { status: "network-error" };
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
        if (response.status === 403 || response.status === 404) {
            // Permanenter Fehler = Zugriff entzogen oder Event geloescht. Den Replay
            // hier NICHT als behebbar behandeln; der nachgelagerte
            // revalidateCachedClient purged den Client (F-10/#1110) — der
            // Sicherheits-Purge darf nicht an einem haengenden Edit scheitern.
            return { status: "revoked", statusCode: response.status };
        }
        // Transiente Fehler (5xx/429): Record behalten, spaeter erneut versuchen.
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
        // halt.
        const records = await _store().listModifiedEvents();
        for (const record of records) {
            const result = await replayModifiedEvent(record);
            if (result.status === "network-error" || result.status === "offline") {
                break;
            }
        }
    }

    // When the browser comes back online, drain the offline-edit queue
    // alongside the generic offline-queue. Independent from
    // offline-queue.js so that an edit never gets silently retried by the
    // generic queue (which cannot handle the 409 body).
    window.addEventListener("online", () => {
        replayAllModifiedEvents();
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", _fireCountEvent);
    } else {
        _fireCountEvent();
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
