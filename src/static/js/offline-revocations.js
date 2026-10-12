/*
 * offline-revocations.js — Teil 3/4 des offline-store-Verbunds (Refs #1838):
 * die personen-genaue Offline-Widerrufsliste (#1760) — der Server meldet
 * anonymisierte Personen, dieser Pfad tilgt ihren kompletten lokalen
 * Bestand (Bundle-Spiegel UND ungesyncte Arbeit), bevor irgendein Replay
 * die geloeschte PII zurueck auf den Server schriebe.
 * Architektur-Uebersicht + Dexie-Schema-Doku: offline-db.js. Laedt NACH
 * offline-bundles.js und VOR offline-store.js (Reihenfolge fix, siehe
 * base.html/offline_base.html).
 */
(function () {
    "use strict";

    // Refs #1838: private Helfer aus offline-db.js — verbrauchbarer
    // Einmal-Kanal (Details + Sicherheitsbegruendung dort). Fail-closed wie
    // in offline-store.js: ohne Kanal bleibt das Modul inert.
    const _internals = window.__offlineStoreInternals && window.__offlineStoreInternals.take();
    if (!_internals) {
        // eslint-disable-next-line no-console
        console.error("[offline-revocations] interner Kanal fehlt — Ladereihenfolge pruefen (Refs #1838).");
        return;
    }
    const { db, listDecrypted } = _internals;

    /* ─── #1760 — personen-genaue Widerrufsliste (Offline-Revocations) ────── */

    /*
     * Refs #1760 (#1476/R20, #1493): Der Server fuehrt eine Liste anonymisierter
     * Personen; dieser Pfad wendet sie auf den lokalen Bestand an.
     *
     * WARUM ein eigener Kanal neben `revalidateCachedClient`: dessen Purge
     * haengt an 401/404/410. Eine ANONYMISIERTE Person existiert aber weiter und
     * liefert 200 — und selbst dieses 200 ueberschreibt den lokalen Klartext
     * NICHT, solange zu ihr ungesyncte Aenderungen offen sind (`"skipped"`).
     * Der lokale 48h-Klartext ueberlebte damit die Loeschung. Die Revoke-Liste
     * ist die positive Gegenrichtung: sie sagt, was WEG MUSS, unabhaengig von
     * der Antwort des Bundle-Endpoints.
     *
     * FAIL-SAFE-ENTSCHEID zu ungesyncter Arbeit: Bundle-Spiegel UND ungesyncte
     * Eintraege (events mit localStatus modified/new/conflict/dead, queue-Rows,
     * Entwuerfe) der Person werden VERWORFEN — nicht als Dead-Letter erhalten.
     * Zwei Gruende:
     *   1. Ein Replay schriebe frische Klartext-PII ueber eine Person, deren
     *      Datensatz gerade nach Art. 17 getilgt wurde — die Loeschung liefe
     *      rueckwaerts. Auf dem Server wuerde er ohnehin auf einen
     *      anonymisierten Datensatz treffen.
     *   2. Ein Dead-Letter behielte den entschluesselbaren Klartext genau dort
     *      liegen, wo der Widerruf ihn entfernen soll.
     * Das ist derselbe Loeschgrund wie der Sicherheits-Purge bei 401/404/410
     * (F-10/#1110, `removeOfflineClient`s `force`-Vertrag) und nutzt bewusst
     * DIESELBE Primitive — kein zweiter Purge-Pfad.
     *
     * SCHLUESSEL-GATE: Der Bundle-Purge braucht keinen Session-Key (planer
     * Dexie-Delete). Queue-/Entwurfs-Rows muessen dagegen entschluesselt werden,
     * um den Personenbezug zu erkennen; ohne Key bleiben sie liegen (sie sind
     * dann ohnehin unlesbarer Chiffretext) und der naechste Lauf holt es nach —
     * die Liste ist ohne Cursor idempotent und deckt das volle Retention-
     * Fenster ab (2x Bundle-TTL).
     */
    function _revocationsUrl() {
        return window.URL_PATTERNS.ENDPOINTS.REVOCATIONS;
    }

    /*
     * True, wenn `haystack` (URL, Formular-Key oder serialisierter Payload) eine
     * der widerrufenen UUIDs nennt. Case-insensitiv: die Server-UUID ist
     * kanonisch klein geschrieben, ein handgeschriebener Formular-Key koennte
     * es nicht sein.
     */
    function _mentionsRevoked(haystack, needlesLower) {
        if (!haystack) return false;
        const text = String(haystack).toLowerCase();
        for (const pk of needlesLower) {
            if (text.indexOf(pk) !== -1) return true;
        }
        return false;
    }

    /*
     * Refs #1774: Die URL eines gequeuten `POST /events/<uuid>/edit/` (analog
     * `/workitems/<uuid>/edit/`) traegt die EREIGNIS-/AUFGABEN-pk, nicht die
     * Personen-UUID; der Body eines Edit-POSTs nennt die Person ebenfalls
     * nicht (nur CSRF, `expected_updated_at`, Feld-Slugs). Ueber
     * `_mentionsRevoked` allein blieb er darum nach einem Widerruf stehen und
     * der naechste Replay schrieb frische Klartext-PII zu einer nach Art. 17
     * getilgten Person.
     *
     * Die lokale `events`-Tabelle kennt die Zuordnung: `pk` und `clientPk`
     * liegen dort als PLAINTEXT-Indexfelder (kein Decrypt, kein Session-Key
     * noetig — der Purge greift damit auch im Idle-Lock). Diese Aufloesung
     * liefert die Record-pks aller lokal bekannten Ereignisse/Aufgaben der
     * widerrufenen Personen; sie werden dem Suchbegriff-Satz des Queue-/
     * Entwurfs-Purges hinzugefuegt.
     *
     * WORKITEM_STATUS-GRENZE (bewusst): Ein gequeuter
     * `POST /partials/workitems/<uuid>/status/` traegt nur die Aufgaben-pk in
     * der URL, keine Personen-UUID im Body. Diese Aufloesung erfasst ihn NUR,
     * wenn die Aufgabe bereits einen `events`-Eintrag hat (`data.kind ===
     * "workitem"`, siehe `_isWorkItemRow`) — also nur, wenn sie offline schon
     * einmal VOLL editiert wurde. Eine reine Bundle-Aufgabe, die nie offline
     * editiert wurde und deren Status offline umgeschaltet wird, hat KEINE
     * `events`-Zeile; ihr Status-POST bleibt nach einem Widerruf zunaechst in
     * der Queue stehen — die serverseitige zweite Linie (#1771) faengt genau
     * diesen Fall ab.
     *
     * REIHENFOLGE (kritisch): Der Aufruf MUSS vor dem Bundle-Purge stehen —
     * `removeOfflineClient(pk, {force: true})` loescht genau die events-Rows,
     * aus denen die Zuordnung stammt. Danach aufgeloest, waere die Tabelle
     * leer und die Luecke wieder offen.
     *
     * REST-GRENZE (bewusst): Ein Edit-POST, dessen Ereignis lokal NICHT (mehr)
     * bekannt ist UND dessen Payload die Personen-UUID nicht traegt, bleibt
     * stehen. Ohne jeden Personenbezug waere sein Verwurf ein Breitband-Purge
     * fremder Arbeit; die serverseitige zweite Linie (#1771) faengt ihn ab.
     */
    async function _localRecordPksOfRevoked(revokedLower) {
        const rows = await db.events.toArray();
        const out = [];
        for (const row of rows) {
            const owner = row && row.clientPk ? String(row.clientPk).toLowerCase() : "";
            if (owner && revokedLower.indexOf(owner) !== -1 && row.pk) {
                out.push(String(row.pk).toLowerCase());
            }
        }
        return out;
    }

    /*
     * `needlesLower` = widerrufene Personen-UUIDs PLUS die lokal aufgeloesten
     * Record-pks ihrer Ereignisse/Aufgaben (siehe `_localRecordPksOfRevoked`).
     * Beide Kanaele zaehlen fuer URL/Formular-Key UND Payload: eine Queue-Row
     * oder ein Entwurf, der eines von beiden nennt, ist Arbeit AN dieser Person.
     */
    async function _purgeRevokedQueueAndDrafts(needlesLower) {
        let purgedQueueEntries = 0;
        let purgedDrafts = 0;
        // `listDecrypted` ueberspringt Rows, die ohne Session-Key nicht lesbar
        // sind (TRANSIENT, Refs #1352) — sie bleiben unangetastet, statt blind
        // geloescht zu werden. Ein permanenter Decrypt-Fehler entwertet sie
        // ohnehin bereits (Auto-Discard/Tombstone).
        const queueRows = await listDecrypted("queue");
        for (const row of queueRows) {
            const payload = JSON.stringify(row.data || {});
            if (_mentionsRevoked(row.url, needlesLower) || _mentionsRevoked(payload, needlesLower)) {
                await db.queue.delete(row.id);
                purgedQueueEntries += 1;
            }
        }
        const draftRows = await listDecrypted("drafts");
        for (const row of draftRows) {
            const payload = JSON.stringify(row.data || {});
            if (_mentionsRevoked(row.formKey, needlesLower) || _mentionsRevoked(payload, needlesLower)) {
                await db.drafts.delete(row.formKey);
                purgedDrafts += 1;
            }
        }
        return { purgedQueueEntries: purgedQueueEntries, purgedDrafts: purgedDrafts };
    }

    // Authentication pages cannot provide a usable revocation list.
    function _revocationAuthKind(response) {
        let path = "";
        try {
            path = new URL(response.url, window.location.origin).pathname;
        } catch (_e) {
            // Missing or invalid URLs do not identify an authentication target.
        }
        const normalized = path.endsWith("/") ? path : path + "/";
        if (["/password-change/", "/password-reset/"].some((prefix) => normalized.startsWith(prefix))) {
            return "password-change";
        }
        if (path === "/mfa" || path.startsWith("/mfa/")) return "mfa";
        if (path === "/login" || path.startsWith("/login/")) return "login";
        const contentType = response.headers?.get?.("Content-Type") || "";
        if (response.redirected && /^text\/html(?:;|$)/i.test(contentType)) return "login";
        return null;
    }

    /*
     * Fetch and apply a fully validated list. Authentication, transport and
     * validation failures preserve local work and cannot authorize replay.
     * Results: ok, error, ratelimited, or auth-pending with kind login/mfa/password-change.
     */
    async function _applyClientRevocations() {
        const empty = { purgedClients: 0, purgedQueueEntries: 0, purgedDrafts: 0 };
        let response;
        try {
            response = await fetch(_revocationsUrl(), {
                method: "GET",
                credentials: "same-origin",
                headers: { Accept: "application/json" },
            });
        } catch (_e) {
            return { status: "error", ...empty };
        }
        if (response.status === 429) {
            return { status: "ratelimited", ...empty };
        }
        if (!response.ok) {
            return { status: "error", ...empty };
        }
        const kind = _revocationAuthKind(response);
        if (kind) return { status: "auth-pending", kind, ...empty };
        let payload;
        try {
            payload = await response.json();
        } catch (_e) {
            // Invalid JSON cannot authorize deletion or replay.
            return { status: "error", ...empty };
        }
        // Validate the complete server contract before applying any deletion.
        const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
        if (!payload || typeof payload !== "object" || Array.isArray(payload) ||
            !Array.isArray(payload.revocations) ||
            !payload.revocations.every((pk) => typeof pk === "string" && uuid.test(pk)) ||
            typeof payload.server_time !== "string" ||
            !/^\d{4}-\d{2}-\d{2}T/.test(payload.server_time) ||
            !Number.isFinite(Date.parse(payload.server_time)) ||
            !Number.isSafeInteger(payload.retention_seconds) || payload.retention_seconds <= 0) {
            return { status: "error", ...empty };
        }
        const pks = payload.revocations;
        if (pks.length === 0) return { status: "ok", ...empty };
        const revokedLower = pks.map((pk) => pk.toLowerCase());

        // Refs #1774: Ereignis->Person ZUERST aufloesen — der Bundle-Purge
        // unten loescht genau die events-Rows, die diese Zuordnung tragen.
        const recordPksLower = await _localRecordPksOfRevoked(revokedLower);

        let purgedClients = 0;
        for (const pk of pks) {
            const key = String(pk);
            const known = await db.clients.where("pk").equals(key).count();
            // `force: true`: ungesyncte Arbeit dieser Person faellt bewusst mit
            // (siehe Fail-Safe-Entscheid im Abschnitts-Kommentar oben).
            // Refs #1838: removeOfflineClient lebt in offline-bundles.js —
            // Aufloesung zur Callzeit ueber die Fassade (wie purgeExpiredBundles
            // in offline-db.js).
            await window.offlineStore.removeOfflineClient(key, { force: true });
            if (known) {
                purgedClients += 1;
            }
        }

        const extra = await _purgeRevokedQueueAndDrafts(revokedLower.concat(recordPksLower));
        const result = { status: "ok", purgedClients: purgedClients, ...extra };
        _fireRevocationEvent(result);
        return result;
    }

    // Refs #2528: shared, non-personal metadata survives page and browser restarts.
    const REVOCATION_STATE_KEY = "revocationReplayState";
    let _storageFailure = false;
    let _approval = null;
    let _authPending = false;

    let _noticeRefresh = 0;

    function _showRevocationNotice(pending) {
        if (typeof document !== "undefined") {
            document.querySelectorAll("[data-offline-revocation-pending]").forEach((node) => {
                node.hidden = !pending || _authPending;
            });
        }
    }

    async function refreshRevocationNotice(checkFailed = false) {
        const refresh = ++_noticeRefresh;
        let row = null;
        let noticePending = true;
        try {
            const store = window.offlineStore;
            const counts = await Promise.all([
                store.countOfflineClients(),
                store.count("queue"),
                store.count("drafts"),
                store.count("events"),
            ]);
            // Read the persisted status after inventory, never carry approval
            // or notice visibility across an inventory await (Refs #2528).
            row = await db.meta.get(REVOCATION_STATE_KEY);
            noticePending = _storageFailure ||
                (counts.some((count) => count > 0) && (checkFailed || !row || row.value?.pending !== false));
        } catch (_e) {
            // Recovery from a read failure requires a fresh persisted check.
            _storageFailure = true;
            _approval = null;
        }
        // An older inventory snapshot must not overwrite a newer notice.
        // Read failures invalidate approval even in a superseded refresh.
        if (refresh === _noticeRefresh || _storageFailure) {
            _showRevocationNotice(noticePending || _storageFailure);
        }
        return row;
    }

    async function isRevocationPending(checkFailed = false) {
        const row = await refreshRevocationNotice(checkFailed);
        const run = window.syncOrchestrator?.getCurrentRun?.();
        // Decide only after every await, from the current error and approval
        // state. Inventory never grants replay permission (Refs #2528).
        return _storageFailure || !run || !_approval || _approval.run !== run ||
            !row || row.value?.pending !== false || row.value.attempt !== _approval.attempt;
    }

    // Refs #2528: refresh after committed inventory changes, including bulk
    // removals, drafts, queue writes and edits. Never grant replay approval.
    const _observedTransactions = new WeakSet();
    function _observeInventoryTransaction(...args) {
        const transaction = args[args.length - 1];
        if (_observedTransactions.has(transaction)) return;
        _observedTransactions.add(transaction);
        transaction.on("complete", function () {
            refreshRevocationNotice();
            window.syncOrchestrator?.broadcast?.({ type: "offline-inventory-changed" });
        });
    }
    for (const table of ["clients", "queue", "drafts", "events"]) {
        for (const hook of ["creating", "updating", "deleting"]) {
            db[table].hook(hook, _observeInventoryTransaction);
        }
    }

    async function _notifyRevocationState() {
        await refreshRevocationNotice();
        window.syncOrchestrator?.broadcast?.({ type: "revocation-state-changed" });
    }

    async function applyClientRevocations() {
        const empty = { purgedClients: 0, purgedQueueEntries: 0, purgedDrafts: 0 };
        let marked = false;
        let authKind = null;
        _storageFailure = true;
        _approval = null;
        _authPending = false;
        const run = window.syncOrchestrator?.getCurrentRun?.();
        try {
            const attempt = window.crypto.randomUUID();
            // Persist before fetching; interrupted checks leave replay suspended.
            await db.meta.put({ key: REVOCATION_STATE_KEY, value: { pending: true, attempt } });
            marked = true;
            _storageFailure = false;
            await _notifyRevocationState();
            const result = await _applyClientRevocations();
            _authPending = result.status === "auth-pending";
            authKind = result.kind;
            if (result.status !== "ok") return result;
            const released = await db.transaction("rw", db.meta, async () => {
                const row = await db.meta.get(REVOCATION_STATE_KEY);
                // An older concurrent success cannot release a newer failed check.
                if (!row || row.value?.attempt !== attempt) return false;
                await db.meta.put({ key: REVOCATION_STATE_KEY, value: { pending: false, attempt } });
                return true;
            });
            if (released && run) _approval = { attempt, run };
            return released ? result : { status: "error", ...empty };
        } catch (_e) {
            if (!marked) {
                // Quota failures must not leave an earlier approval usable on reload.
                // Only invalidate control metadata; encrypted work remains intact.
                try { await db.meta.delete(REVOCATION_STATE_KEY); } catch (_ignored) {}
            }
            return { status: "error", ...empty };
        } finally {
            // Every fresh check, including direct saves and conflict choices,
            // selects the shared notice before returning to its caller.
            await window.offlineEdit?.signalAuthPending?.(_authPending, authKind);
            await _notifyRevocationState();
        }
    }

    /*
     * Refs #2382: EINE Quelle fuer alle drei Aufrufer (sync-orchestrator.js
     * Sync-Sequenz + Boot-Check, offline-store.js Fallback-Listener) statt
     * eines eigenen Signals je Aufrufer. Der bisherige Zustand liess das
     * Ergebnis von applyClientRevocations verworfen liegen — die offene
     * Offline-Personenansicht einer gerade widerrufenen Person erfuhr nichts
     * davon und zeigte weiter das (inzwischen falsche) "wird synchronisiert".
     *
     * DATENSPARSAMKEIT: ``detail`` traegt ausschliesslich Zaehlwerte, keine
     * Personen-Identifikatoren. Eine offene Offline-Personenansicht kennt
     * ihre eigene pk bereits aus der URL und prueft nach diesem Signal selbst
     * lokal in IndexedDB nach, ob ihr Datensatz noch vorhanden ist (siehe
     * offline-client-view.js) — das Ereignis muss dafuer niemanden nennen.
     *
     * Feuert NUR, wenn tatsaechlich etwas geloescht wurde — eine leere Liste,
     * ein Transportfehler oder ein Rate-Limit (alle drei liefern ``empty``
     * unveraendert) lassen den Bestand unangetastet und damit auch keine
     * offene Ansicht betroffen; ebenso eine nicht-leere Liste, die lokal auf
     * nichts trifft (rein remote bekannte Personen).
     */
    function _fireRevocationEvent(result) {
        const changed = result.purgedClients > 0 || result.purgedQueueEntries > 0 || result.purgedDrafts > 0;
        if (!changed || typeof window === "undefined" || !window.dispatchEvent) return;
        window.dispatchEvent(
            new CustomEvent("offline-client-revocations-applied", {
                detail: {
                    purgedClients: result.purgedClients,
                    purgedQueueEntries: result.purgedQueueEntries,
                    purgedDrafts: result.purgedDrafts,
                },
            })
        );
    }

    // Refs #1838: Export dieser Datei auf die EINE Fassade (siehe offline-db.js).
    window.offlineStore = Object.assign(window.offlineStore || {}, {
        // Refs #1760 — personen-genaue Widerrufsliste (Sync + Boot-Check)
        applyClientRevocations: applyClientRevocations,
        isRevocationPending: isRevocationPending,
    });
})();
