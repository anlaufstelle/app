/*
 * Offline-Edit Orchestrator (Stage 3, Refs #575, #572).
 *
 * Bridges the offline-read-cache (Stage 2) and the generic offline-queue
 * (Stage 1): when the user edits an event while offline, this module writes
 * the edited state into IndexedDB under `localStatus: "modified"` plus the
 * `expected_updated_at` token from the cached copy. When the network returns
 * we replay the edit against `/events/<pk>/edit/` with an
 * `Accept: application/json` header plus the replay marker
 * `X-Offline-Replay: 1`; the server honours that with either the
 * confirmation marker `X-Offline-Replay-Result: saved` (success, Refs #2289
 * — a bare redirect no longer counts, it may end on the login/MFA/password
 * page) or a 409 JSON body (conflict) that we stash back into IndexedDB
 * under `localStatus: "conflict"` for the merge UI to pick up.
 *
 * This module deliberately does NOT use the generic queue — offline edits
 * must round-trip through a dedicated replay because they need to read the
 * 409 response body and carry per-event status information, both of which
 * the queue's fire-and-forget model does not provide.
 *
 * Refs #1398 (P2): WorkItem-Records fahren als paralleler Track durch
 * dieselbe Maschinerie — Diskriminator `kind: "workitem"` liegt IM
 * verschluesselten `data`-Envelope (nie top-level: saveOfflineEdit
 * persistiert nur {pk, clientPk, occurredAt, localStatus, data}, ein
 * Top-Level-Feld wuerde gedroppt). Die Records teilen sich die
 * `events`-Tabelle, damit die #1389-gehaerteten Status-Ops
 * (updateEventLocalStatus/markEventDead/saveConflictState/clearOfflineEdit/
 * countUnsyncedEvents) per pk unveraendert greifen. Replay geht gegen
 * /workitems/new/ bzw. /workitems/<pk>/edit/; Klassifikation und
 * Store-Side-Effects sind identisch zum Event-Pfad (ADR-030).
 */
(function () {
    "use strict";

    function _store() {
        if (!window.offlineStore) {
            throw new Error("OfflineStoreNotLoaded");
        }
        return window.offlineStore;
    }

    // Refs #1408: die gemeinsame CSRF-Logik (fromMeta/refresh) lebt in
    // csrf-utils.js (window.csrfUtils). Zur CALL-Zeit aufloesen und tolerant
    // bleiben, falls das Util wider Erwarten fehlt (kein Crash). refresh()
    // holt den frischen Token vom dedizierten Endpoint statt per Regex aus
    // /login/-HTML (das fragile Muster aus #1330/#1332) — bei !ok/Netzfehler
    // null, sodass der 403-Retry unten sauber unterbleibt statt mit einem
    // stale Token weiterzumachen.
    function _csrfFromMeta() {
        return window.csrfUtils ? window.csrfUtils.fromMeta() : null;
    }

    async function _refreshCsrf() {
        return window.csrfUtils ? window.csrfUtils.refresh() : null;
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
                //
                // Refs #2289: Der WERT ist die Protokollversion des Replays.
                // "2" heisst: dieser Client versteht die Erfolgsbestaetigung
                // (``X-Offline-Replay-Result: saved``) und erwartet sie STATT
                // des Redirects. Ein Geraet mit vorgehaltenem v48-Precache
                // sendet weiterhin "1" und bekommt vom neuen Server byte-gleich
                // den bisherigen Redirect-Kontrakt — sonst deutete das alte
                // Skript die 200-Bestaetigung als "invalid"/"error" und meldete
                // tatsaechlich gespeicherte Arbeit dauerhaft als
                // unsynchronisiert. Das SW-Update haengt an einem Nutzerklick
                // (sw.js: skipWaiting nur auf SKIP_WAITING), die Umstellung
                // muss also schnittfrei sein.
                "X-Offline-Replay": REPLAY_PROTOCOL_VERSION,
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
    // mit dem frisch vom dedizierten CSRF-Endpoint geholten Token (Refs #1408,
    // _refreshCsrf oben) nachfassen, bevor der Replay als "revoked" gilt. Gibt
    // null zurück, wenn das Netz wegbricht.
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

    /*
     * Refs #2289 — Vertrag „Erfolgsbestaetigung" des Offline-Replays.
     *
     * (1) Erfolg NUR mit Marker: ein Schreibvorgang gilt ausschliesslich dann
     *     als gespeichert, wenn die Antwort auf den mit ``X-Offline-Replay: 1``
     *     markierten Request den Bestaetigungsmarker
     *     ``X-Offline-Replay-Result: saved`` traegt. Den setzen allein die
     *     Create-/Edit-Endpunkte NACH einem festgeschriebenen Schreibvorgang
     *     (oder bei einem Idempotenz-Treffer auf einen bereits
     *     festgeschriebenen). Eine blosse Weiterleitung belegt gar nichts:
     *     ``fetch`` folgt ihr transparent, und die Endantwort kann die
     *     Anmelde-, Zwei-Faktor- oder Passwortwechsel-Seite sein.
     * (2) Ausstehende Authentifizierung: Antworten, die auf ``/login/``,
     *     ``/mfa/`` (Setup UND Verify), ``/password-change/`` oder
     *     ``/password-reset/`` fuehren — als gefolgter Redirect, als
     *     ``HX-Redirect``-Header oder als ``Location`` eines ungefolgten 3xx —
     *     lassen die lokalen Daten UNVERAENDERT und halten den Batch an.
     *
     * Bis #2289 galt jede Weiterleitung ausser ``/login/`` als Erfolg: die
     * MFA-Middleware leitet Schreibanfragen VOR der View nach ``/mfa/setup/``
     * bzw. ``/mfa/verify/`` um (z.B. nach einer waehrend der Offline-Phase
     * geaenderten Pflicht), die Speicherung fand nie statt — und der
     * Create-Replay loeschte die lokale Neuanlage trotzdem.
     *
     * Dieselben Praedikate liegen wortgleich in offline-queue.js (die beiden
     * Module teilen bewusst keinen Namensraum, siehe _csrfFromMeta/_postForm);
     * tests/js/offline-replay-auth.test.mjs haelt beide Fassungen deckungsgleich.
     */
    const AUTH_PENDING_PREFIXES = ["/login/", "/mfa/", "/password-change/", "/password-reset/"];
    // Refs #2289: Zielklassen fuer die Anzeige. Der erzwungene Passwortwechsel
    // ist KEIN blosses "melde dich neu an": er rotiert den Schluessel-Salt, der
    // lokale Bestand wird danach unlesbar und purgt (crypto.js) — und vorher
    // uebertragen laesst er sich nicht, weil JEDER Schreib-Replay genau auf
    // /password-change/ bounct. Die Anzeige braucht deshalb die Ursache.
    const AUTH_PENDING_PASSWORD_PREFIXES = ["/password-change/", "/password-reset/"];
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
            path = new URL(rawUrl, window.location.origin).pathname;
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

    function _eventEditUrl(eventPk) {
        return "/events/" + encodeURIComponent(eventPk) + "/edit/";
    }

    function _workItemEditUrl(workItemPk) {
        return "/workitems/" + encodeURIComponent(workItemPk) + "/edit/";
    }

    /*
     * Refs #1398 (P2): Record-Diskriminator fuer den WorkItem-Track. Event-
     * Records tragen nie `kind: "workitem"` — jede Verzweigung auf dieses
     * Praedikat laesst den Event-Pfad verhaltensgleich.
     */
    function _isWorkItemRecord(record) {
        return Boolean(record && record.data && record.data.kind === "workitem");
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
     * Refs #2289: Nutzerhinweis „Anmeldung erforderlich".
     *
     * Die Klassifikation liegt in beiden Replay-Modulen, die Darstellung
     * bewusst nur hier: offline-edit.js laedt auf JEDER Seite mit
     * Offline-Faehigkeit (base.html UND offline_base.html) und zwar NACH
     * offline-queue.js. Beide Module melden den Zustand ueber das
     * window-Event ``offline-auth-pending`` (detail: {pending, count}); der
     * Empfaenger unten schaltet jedes ``[data-offline-auth-pending]``-Element
     * sichtbar und setzt den uebersetzten Text aus dessen
     * ``data-auth-pending-text`` (Platzhalter ``{count}`` — Muster wie
     * offline-client-badge.js/detail.html). Ohne solches Element passiert
     * nichts; die Module bleiben seitenunabhaengig.
     */
    async function _countLocalPending() {
        let total = 0;
        try {
            total += (await _store().countUnsyncedEvents()) || 0;
        } catch (_e) {
            /* Zaehler bleibt bei dem, was sich lesen liess */
        }
        try {
            const store = _store();
            if (typeof store.countQueueByStatus === "function") {
                const breakdown = (await store.countQueueByStatus()) || {};
                total += breakdown.pending || 0;
            }
        } catch (_e) {
            /* dito */
        }
        return total;
    }

    function _signalAuthPending(pending, kind) {
        const active = Boolean(pending);
        _countLocalPending()
            .then((count) => {
                window.dispatchEvent(
                    new CustomEvent("offline-auth-pending", {
                        detail: { pending: active, count: count, kind: active ? kind || "login" : null },
                    })
                );
            })
            .catch(() => {});
    }

    function _renderAuthPending(active, count, kind) {
        let nodes;
        try {
            nodes = document.querySelectorAll("[data-offline-auth-pending]");
        } catch (_e) {
            return;
        }
        nodes.forEach((node) => {
            // Refs #2289: Der Passwortwechsel-Zweig traegt einen eigenen Text —
            // „melden Sie sich erneut an, dann geht es weiter" waere dort falsch
            // (der Wechsel loescht den lokalen Bestand). Fehlt das Attribut,
            // bleibt der allgemeine Text.
            const attribute =
                kind === "password-change" && node.hasAttribute("data-auth-pending-password-text")
                    ? "data-auth-pending-password-text"
                    : "data-auth-pending-text";
            const template = node.getAttribute(attribute) || "";
            if (template) {
                const target = node.querySelector("[data-offline-auth-pending-text]") || node;
                target.textContent = template.replace("{count}", String(count));
            }
            if (active) node.removeAttribute("hidden");
            else node.setAttribute("hidden", "hidden");
        });
    }

    window.addEventListener("offline-auth-pending", (event) => {
        const detail = (event && event.detail) || {};
        _renderAuthPending(Boolean(detail.pending), detail.count || 0, detail.kind);
    });

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
        await window.crypto_session?.awaitReady?.();
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
        // Refs #2293: Bei einer noch nie synchronisierten Neuanlage liegen die
        // Create-Metadaten (Fall, Dokumentationstyp, Zeitpunkt, Idempotenz-
        // Schluessel) AUSSCHLIESSLICH im gespeicherten Envelope — der
        // Create-Replay (replayNewEvent) baut ``client``/``document_type``/
        // ``case``/``occurred_at`` allein daraus. saveOfflineEdit ersetzt die
        // Row vollstaendig (kein Merge), und die Bearbeiten-Oberflaeche kennt
        // die Fall-Zuordnung gar nicht (sie steht in keinem normalisierten
        // Anzeigefeld, das der Aufrufer zurueckgeben koennte). Ohne diese
        // Uebernahme entfernt schon eine kleine Feldkorrektur VOR der ersten
        // Synchronisation die bewusst gewaehlte Fall-Zuordnung — das Ereignis
        // landet ausserhalb des Falls und fehlt im Fallverlauf.
        const previous = (wasNew && existing.data) || {};
        const occurredAt = opts.occurredAt || previous.occurredAt || (wasNew ? existing.occurredAt || "" : "");
        const data = {
            formData: formData,
            expectedUpdatedAt: token,
            // Snapshot a few metadata fields so the list/review UI can
            // label the edit without cross-referencing the cache. Refs
            // #1111: ``documentTypePk`` + ``occurredAt`` let the offline
            // viewer re-render and re-edit a still-unsynced event (the
            // clean bundle record is overwritten by this edit envelope).
            documentTypeName: opts.documentTypeName || previous.documentTypeName || "",
            documentTypePk: opts.documentTypePk || previous.documentTypePk || "",
            occurredAt: occurredAt,
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
            data.idempotencyKey = previous.idempotencyKey || _uuid();
            // Refs #2293: Fall-Zuordnung der Neuanlage erhalten. Ein leerer
            // Wert bleibt leer — ein Ereignis ohne Fall bekommt durch einen
            // Re-Edit keinen.
            data.casePk = opts.casePk || previous.casePk || "";
        }
        const record = {
            pk: pk,
            clientPk: opts.clientPk || (wasNew ? existing.clientPk || "" : ""),
            occurredAt: occurredAt,
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
     * Flache formData (slug → value) in URLSearchParams uebertragen. Refs
     * #1111: MULTI_SELECT-Felder tragen ein Array — jeden Wert als eigenen
     * Key anhaengen, damit Djangos MultipleChoiceField
     * (``value_from_datadict`` → ``getlist``) sie als Liste sieht statt als
     * einen kommaverbundenen String (= „ungültige Auswahl"). Refs #1398:
     * aus replayNewEvent/replayModifiedEvent extrahiert (dort identische
     * Schleifen), damit der WorkItem-Track sie mitnutzt — verhaltensneutral.
     */
    function _appendFormFields(form, fd) {
        for (const [key, value] of Object.entries(fd || {})) {
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
    }

    /*
     * Gemeinsame Antwort-Klassifikation fuer Create-Replays — genutzt von
     * replayNewEvent UND replayNewWorkItem (Refs #1398), nach dem
     * HTTP-Replay-Contract (ADR-030). Store-Side-Effects laufen zentral hier:
     * Erfolg raeumt den Record ab, 404/410 dead-lettert ihn.
     */
    async function _classifyCreateResponse(record, response, options) {
        // Refs #1351 (Bug #2) / #2289: Eine Weiterleitung zur Anmeldung, zur
        // Zwei-Faktor-Abfrage oder zum Passwortwechsel ist KEIN Erfolg — die
        // Speicherung fand nie statt, fetch folgt dem 302 aber transparent bis
        // zur Auth-Seite. Ohne diesen Guard wuerde der nie serverseitig
        // angelegte Record hier als "synced" geloescht (stiller Datenverlust).
        // Record UNVERAENDERT lassen, kein clearOfflineEdit; der Aufrufer
        // (replayAllModifiedEvents) haelt den Batch an (symmetrisch zur
        // generischen Queue, HTTP-Replay-Contract).
        const authKind = _authPendingResponseKind(response);
        if (authKind) {
            _signalAuthPending(true, authKind);
            return { status: "auth-pending", authKind: authKind };
        }
        // Refs #2289: Erfolg ausschliesslich mit Serverbestaetigung.
        if (_isSavedConfirmed(response)) {
            _signalAuthPending(false);
            await _store().clearOfflineEdit(record.pk);
            _fireCountEvent();
            return { status: "synced" };
        }
        if (response.redirected || response.status === 302) {
            // Refs #2289: Weiterleitung ohne Bestaetigungsmarker — kein Beleg
            // fuer ein Schreiben. Record BEHALTEN (der Idempotenzschluessel
            // verhindert eine Doppelanlage, falls doch geschrieben wurde) und
            // den Batch anhalten: jeder weitere Record traefe dieselbe Lage.
            return { status: "unconfirmed", statusCode: response.status };
        }
        if (response.status === 403) {
            // Refs #1394: 403 bleibt "revoked" (kann CSRF-/Rate-Limit-/
            // Proxy-Rauschen sein; seit #1354 KEIN Purge-Trigger, bei echtem
            // Rechteentzug vernichtet die Salt-Rotation den Bestand) —
            // Record bleibt "new", der naechste Reconnect versucht erneut.
            return { status: "revoked", statusCode: response.status };
        }
        if (response.status === 404 || response.status === 410) {
            // Refs #1394 (ADR-030): das Create-Ziel existiert serverseitig
            // dauerhaft nicht (Klient geloescht/Route weg) — PERMANENT, wie
            // im Edit-Pfad (replayModifiedEvent). Vorher kollabierte 404 in
            // ein endloses "revoked"-Retry und 410 in den transienten
            // error-Bucket. markEventDead merkt sich `wasNew`; ein Nutzer-Retry
            // aus der Dead-Letter-UI reaktiviert einen not-found-Record aber
            // bewusst als "modified" (Edit-Pfad), NICHT als "new": ist das Ziel
            // dauerhaft weg, wuerde ein erneuter Create-POST genauso 404en. Nur
            // fuer reparable Gruende (invalid/unexpected-response) schickt
            // retryDeadEvent einen wasNew-Record zurueck auf den Create-Pfad.
            await _store().markEventDead(record.pk, "not-found", "" + response.status);
            _fireCountEvent();
            return { status: "dead", deadReason: "not-found", statusCode: response.status };
        }
        if (response.status === 429) {
            // Refs #1351/#1384: Ratelimit ist user-global (analog
            // offline-queue.js) — Record unangetastet lassen (bleibt "new"),
            // der Aufrufer (replayAllModifiedEvents) bricht den Rest des
            // Batches ab statt das Ratelimit-Budget weiter zu verbrennen.
            return { status: "ratelimited", statusCode: response.status };
        }
        if (response.status === 422) {
            // Refs #2187: Beim pk-losen Create-Replay ist ein 422 PERMANENT,
            // weil der Nutzer die Eingabe offline nicht mehr korrigieren kann
            // (kein Server-Re-Render, keine Feld-UI im Create-Kontext). Der
            // Record wird ins Dead-Letter geschoben; Feldfehler-Codes
            // (PII-frei) gehen mit, damit die Dead-Letter-Liste den Grund
            // anzeigen kann. Der Edit-Pfad (replayModifiedEvent) bleibt
            // unveraendert: dort ist 422 reparabel.
            let body = null;
            try {
                body = await response.json();
            } catch (_e) {
                body = {};
            }
            const fieldErrors = (body && body.errors) || {};
            // Der synchrone Create in der Offline-Akte hat weiterhin eine
            // sichtbare Formular-UI und muss die Serverfehler dort anzeigen.
            // Nur der unbeaufsichtigte Reconnect-Replay dead-lettert.
            if (!options || options.replay !== true) {
                return { status: "invalid", errors: fieldErrors };
            }
            // Refs #2187: Nur Feldnamen + Fehlercodes ins Dead-Letter-Detail;
            // serverseitige Meldungen koennen Eingabewerte enthalten.
            const errorCodes = Object.fromEntries(
                Object.entries(fieldErrors).map(([key, items]) => [
                    key,
                    Array.isArray(items) ? items.map((item) => (item && item.code) || "").filter(Boolean) : [],
                ])
            );
            await _store().markEventDead(record.pk, "invalid", JSON.stringify(errorCodes));
            _fireCountEvent();
            return { status: "dead", deadReason: "invalid", errors: errorCodes, statusCode: response.status };
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

    /*
     * Replay eines offline neu angelegten Events gegen ``/events/new/``.
     * Erfolg = Redirect auf die Event-Detailseite. Refs #1351/#1384: nach M11
     * antwortet die Create-View auf ein ungueltiges Formular (roher
     * ``Accept: application/json``) mit 422+errors — der bestehende
     * 200-Re-Render-Fallback (aeltere/HX-Faelle) bleibt zusaetzlich erhalten.
     * Bei Erfolg den lokalen "new"-Record entfernen — die naechste
     * Re-Validierung holt das kanonische Server-Event mit seiner echten pk
     * ins Bundle. Antwort-Klassifikation: _classifyCreateResponse (geteilt
     * mit dem WorkItem-Track, Refs #1398).
     */
    async function replayNewEvent(record, csrf, options) {
        const data = record.data || {};
        const form = new URLSearchParams();
        if (record.clientPk) form.set("client", record.clientPk);
        if (data.documentTypePk) form.set("document_type", data.documentTypePk);
        // Refs #1397: Fall-Zuordnung mitschicken (Feldname wie EventMetaForm).
        if (data.casePk) form.set("case", data.casePk);
        if (data.occurredAt) form.set("occurred_at", data.occurredAt);
        _appendFormFields(form, data.formData);
        const response = await _postFormWithCsrfRetry("/events/new/", form.toString(), csrf, {
            "X-Idempotency-Key": data.idempotencyKey || record.pk,
        });
        if (!response) {
            return { status: "network-error" };
        }
        return await _classifyCreateResponse(record, response, options);
    }

    /*
     * Refs #1398 (P2): Ein offline NEU angelegtes WorkItem in IndexedDB
     * ablegen — Spiegel von markEventNew: localStatus "new" mit
     * client-generierter pk, Idempotenz-Key gegen Doppel-Anlage bei
     * Verbindungsabbruch nach dem Server-Write (F-09/#1109, #1329).
     * `formData` traegt die WorkItemForm-Felder flach (item_type, title,
     * description, priority, due_date, remind_at, recurrence, assigned_to);
     * `kind: "workitem"` in `data` waehlt beim Replay den
     * /workitems/new/-Track.
     */
    async function markWorkItemNew(clientPk, formData, options) {
        const opts = options || {};
        const record = {
            pk: opts.pk || _uuid(),
            clientPk: clientPk || "",
            occurredAt: "",
            localStatus: "new",
            data: {
                kind: "workitem",
                formData: formData,
                idempotencyKey: opts.idempotencyKey || _uuid(),
                lastEditedAt: Date.now(),
            },
        };
        // Refs #1356: offline neu angelegte Eintraege existieren serverseitig
        // noch gar nicht — das schuetzenswerteste Gut. Fire-and-forget um
        // dauerhaften Speicher bitten (Eviction-Schutz), OHNE das Anlegen zu
        // blockieren (analog markEventNew).
        window.offlineStore.ensurePersistentStorage().catch(function () {});
        await _store().saveOfflineEdit(record);
        _fireCountEvent();
        return record;
    }

    /*
     * Refs #1398 (P2): Ein offline bearbeitetes WorkItem in IndexedDB
     * ablegen — Spiegel von markEventModified fuer den WorkItem-Track.
     *
     * `expectedUpdatedAt` (Optimistic-Lock-Token) ist das
     * WorkItem-`updated_at` aus dem Bundle (Refs #1398 P1) und kommt vom
     * Aufrufer. Anders als bei Events gibt es KEINEN CLEAN-Row-Fallback in
     * der events-Tabelle (WorkItems liegen im clients-Envelope) — bei einem
     * Re-Edit bleibt der Token des vorherigen Edit-Envelopes erhalten;
     * fehlt er ganz, antwortet der Server mit 409 "missing-token"
     * (Konflikt-Flow, #1338/#1351) statt still Last-Write-Wins.
     */
    async function markWorkItemModified(workItemPk, formData, options) {
        const opts = options || {};
        const pk = String(workItemPk);
        // Refs #1351 (analog markEventModified): ein offline NEU angelegtes,
        // noch nie synchronisiertes WorkItem (localStatus "new") darf durch
        // einen Re-Edit NICHT zu "modified" degradieren — sonst zielte jeder
        // weitere Replay dauerhaft auf die serverseitig nie existierende
        // /workitems/<pk>/edit/-URL statt auf /workitems/new/ (S1-Invariante
        // „unsynced stirbt nie still").
        const existing = await _store().getOfflineEvent(pk);
        const wasNew = Boolean(existing && existing.localStatus === "new");
        let token = opts.expectedUpdatedAt || "";
        if (!token && existing && existing.data) {
            token = existing.data.expectedUpdatedAt || "";
        }
        const data = {
            kind: "workitem",
            formData: formData,
            expectedUpdatedAt: token,
            lastEditedAt: Date.now(),
        };
        if (wasNew) {
            // Refs #1351 (analog markEventModified): der Idempotenzschutz der
            // urspruenglichen Neuanlage muss den Re-Edit ueberleben — sonst
            // schickt der naechste Replay ein ANDERES Idempotency-Key mit und
            // der Doppel-Anlage-Schutz greift nicht mehr.
            data.idempotencyKey = (existing.data && existing.data.idempotencyKey) || _uuid();
        }
        const record = {
            pk: pk,
            // Refs #2293 (gleiche Klasse wie im Event-Pfad): ein offline NEU
            // angelegtes WorkItem replayt via /workitems/new/, wo ``client``
            // ausschliesslich aus ``record.clientPk`` stammt — bleibt der
            // Aufrufer ihn schuldig, aus der bestehenden Row uebernehmen
            // statt ihn zu verlieren.
            clientPk: opts.clientPk || (wasNew ? existing.clientPk || "" : ""),
            occurredAt: "",
            localStatus: wasNew ? "new" : "modified",
            data: data,
        };
        await _store().saveOfflineEdit(record);
        _fireCountEvent();
        return record;
    }

    /*
     * Refs #1398 (P2): Replay eines offline neu angelegten WorkItems gegen
     * ``/workitems/new/`` — Spiegel von replayNewEvent. `client` geht als
     * hidden-pk-Feld mit (WorkItemForm), alle uebrigen Formfelder liegen
     * flach in `formData`. Idempotenz-Key-Konvention identisch zum
     * Event-Pendant (#1329): der beim Anlegen geminte Key bleibt ueber
     * Re-Edits/Retries stabil.
     */
    async function replayNewWorkItem(record, csrf, options) {
        const data = record.data || {};
        const form = new URLSearchParams();
        if (record.clientPk) form.set("client", record.clientPk);
        _appendFormFields(form, data.formData);
        const response = await _postFormWithCsrfRetry("/workitems/new/", form.toString(), csrf, {
            "X-Idempotency-Key": data.idempotencyKey || record.pk,
        });
        if (!response) {
            return { status: "network-error" };
        }
        return await _classifyCreateResponse(record, response, options);
    }

    async function replayModifiedEvent(record, options) {
        if (!navigator.onLine) return { status: "offline" };
        await window.crypto_session?.awaitReady?.();
        if (!window.crypto_session || !window.crypto_session.hasSessionKey()) {
            return { status: "no-key" };
        }

        let csrf = _csrfFromMeta();
        if (!csrf) csrf = await _refreshCsrf();

        // Refs #1323: offline NEU angelegte Eintraege gehen an den Create-
        // statt den /edit/-Pfad (dessen pk serverseitig gar nicht existiert).
        // Refs #1398 (P2): der `kind`-Diskriminator in `data` waehlt den
        // WorkItem-Track; Events tragen nie `kind: "workitem"`.
        if (record.localStatus === "new") {
            if (_isWorkItemRecord(record)) {
                return await replayNewWorkItem(record, csrf, options);
            }
            return await replayNewEvent(record, csrf, options);
        }

        const form = new URLSearchParams();
        _appendFormFields(form, record.data && record.data.formData);
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

        // Refs #1398 (P2): der Edit-Pfad ist generisch — nur die Ziel-URL
        // haengt am `kind` (WorkItem: /workitems/<pk>/edit/, Event:
        // /events/<pk>/edit/); Klassifikation + Store-Side-Effects darunter
        // sind fuer beide identisch (HTTP-Replay-Contract, ADR-030).
        const editUrl = _isWorkItemRecord(record) ? _workItemEditUrl(record.pk) : _eventEditUrl(record.pk);
        const response = await _postFormWithCsrfRetry(editUrl, form.toString(), csrf);
        if (!response) {
            return { status: "network-error" };
        }

        // Refs #1351 (Bug #2) / #2289: symmetrisch zu replayNewEvent — eine
        // Weiterleitung zur Anmeldung, zur Zwei-Faktor-Abfrage oder zum
        // Passwortwechsel ist KEIN Erfolg (fetch folgt dem 302 transparent bis
        // zur Auth-Seite). Ohne diesen Guard wuerde der Erfolgszweig unten den
        // nie serverseitig gespeicherten Edit auf "clean" setzen (raus aus dem
        // Unsynced-Set = stiller Datenverlust bei blossem Session-Ablauf bzw.
        // bei einer waehrend der Offline-Phase geaenderten MFA-Pflicht).
        // Record UNVERAENDERT lassen; der Aufrufer (replayAllModifiedEvents)
        // haelt den Batch an (HTTP-Replay-Contract, constraints.md Z.29).
        const authKind = _authPendingResponseKind(response);
        if (authKind) {
            _signalAuthPending(true, authKind);
            return { status: "auth-pending", authKind: authKind };
        }

        // Refs #2289: Erfolg ist NUR ein belegtes Speichern — die Antwort auf
        // den Replay traegt den Bestaetigungsmarker der Schreib-View. Ein
        // blankes 200 ist KEIN Erfolg (Refs #1111: ein ungueltiges Formular kam
        // frueher als re-rendertes 200 zurueck und wurde faelschlich als
        // "synced" verworfen; der Server liefert dafuer jetzt 422), und eine
        // blosse Weiterleitung ebenfalls nicht mehr.
        if (_isSavedConfirmed(response)) {
            _signalAuthPending(false);
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
        if (response.redirected || response.status === 302) {
            // Refs #2289: Weiterleitung ohne Bestaetigungsmarker — kein Beleg
            // fuer ein Schreiben (unbekanntes Zwischenziel, aelterer Server).
            // Edit BEHALTEN statt ihn auf "clean" zu setzen; der Aufrufer
            // haelt den Batch an.
            return { status: "unconfirmed", statusCode: response.status };
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
        await window.crypto_session?.awaitReady?.();
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
            const result = await replayModifiedEvent(record, { replay: true });
            if (
                result.status === "network-error" ||
                result.status === "offline" ||
                result.status === "ratelimited" ||
                result.status === "auth-pending" ||
                // Refs #2289: unbestaetigte Weiterleitung — dieselbe Lage
                // traefe jeden weiteren Record; anhalten statt blind
                // weiterzuschreiben.
                result.status === "unconfirmed"
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
    //
    // Refs #1409/#1828: base.html laedt sync-orchestrator.js NACH
    // offline-edit.js — direkt registrieren geht hier also (noch) nicht.
    // Statt des frueher pro Konsument duplizierten DOMContentLoaded/
    // naechster-Tick-Retry-Geruests wandert der Empfaenger in die
    // Pending-Liste, die sync-orchestrator.js beim Laden draint (und danach
    // als Sofort-Registrierung uebernimmt, siehe dort).
    function _onOrchestratorMessage(msg) {
        if (msg && msg.type === "sync-finished") _fireCountEvent();
    }
    if (window.syncOrchestrator && window.syncOrchestrator.onMessage) {
        window.syncOrchestrator.onMessage(_onOrchestratorMessage);
    } else {
        (window.syncOrchestratorPending = window.syncOrchestratorPending || []).push(
            _onOrchestratorMessage
        );
    }

    window.offlineEdit = {
        markEventModified: markEventModified,
        markEventNew: markEventNew,
        // Refs #1398 (P2): WorkItem-Track — gleiche Tabelle/Status-Ops wie
        // Events, eigene Replay-Endpunkte via `kind`-Diskriminator in `data`.
        markWorkItemModified: markWorkItemModified,
        markWorkItemNew: markWorkItemNew,
        replayModifiedEvent: replayModifiedEvent,
        replayAllModifiedEvents: replayAllModifiedEvents,
        enqueueConflictForReview: enqueueConflictForReview,
        refreshCounts: _fireCountEvent,
        // Refs #2289 (TST, Muster wie offlineQueue._backoffFor/_isReady): die
        // sicherheitskritischen Klassifikations-Praedikate isoliert testbar —
        // tests/js/offline-replay-auth.test.mjs haelt sie deckungsgleich mit
        // den wortgleichen Fassungen in offline-queue.js.
        _isAuthPendingTarget: _isAuthPendingTarget,
        _isAuthPendingResponse: _isAuthPendingResponse,
        _authPendingKind: _authPendingKind,
        _authPendingResponseKind: _authPendingResponseKind,
        _isSavedConfirmed: _isSavedConfirmed,
    };
})();
