/*
 * offline-store.js — Teil 4/4 des offline-store-Verbunds (Refs #1838):
 * Local-Status-Lifecycle der Events, Dead-Letter, Queue-/Tally-Sicht,
 * Storage-Quota/Persist sowie die online-/appinstalled-Listener.
 * Architektur-Uebersicht + Dexie-Schema-Doku: offline-db.js. Laedt als
 * LETZTE Datei der festen Reihenfolge (offline-db → offline-bundles →
 * offline-revocations → offline-store) und raeumt den internen Kanal ab.
 *
 * Refs #573, #576, #618, #1351, #1110 — Historie siehe offline-db.js.
 */
(function () {
    "use strict";

    // Refs #1838: private Helfer aus offline-db.js — verbrauchbarer
    // Einmal-Kanal (Details + Sicherheitsbegruendung dort). Fail-closed:
    // ohne Kanal (offline-db.js fehlt / Ladereihenfolge verletzt /
    // Injektionsversuch) bleibt das Modul inert. Der Ausfall eines
    // MITTLEREN Moduls ist damit NICHT erkannt (das take()-Budget zaehlt
    // Abholungen, nicht Abholer) — den macht der Vollstaendigkeits-Check
    // am Dateiende fail-loud (Review-Befund #1838).
    const _internals = window.__offlineStoreInternals && window.__offlineStoreInternals.take();
    // Refs #1838: letzter Verbraucher — Slot IMMER abraeumen, auch im
    // Fail-closed-Fall darf nichts liegen bleiben.
    delete window.__offlineStoreInternals;
    if (!_internals) {
        // eslint-disable-next-line no-console
        console.error("[offline-store] interner Kanal fehlt — Ladereihenfolge pruefen (Refs #1838).");
        return;
    }
    const { db, _crypto, _aad, _isTransientDecryptError, _isQuotaError, putEncrypted, getDecrypted, listDecrypted, purgeExpired } =
        _internals;


    /* ─── Stage 3 (#575) — Local-Status-Tracking on events ───────────────── */

    /*
     * Persist or update a single offline-edited event. Writes into the
     * existing `events` table (schema v2) and keeps `localStatus` in a
     * clear, `modified`, `new`, `conflict` or `synced` lifecycle. The
     * payload includes `expectedUpdatedAt` so the replay can hand the
     * optimistic-concurrency token back to the server.
     *
     * `record.pk` may be an existing offline-cached event pk or a client-
     * side-generated UUID for offline-created events (localStatus="new").
     */
    async function saveOfflineEdit(record) {
        const crypto = _crypto();
        if (!record || !record.pk) throw new Error("MalformedEdit");
        const { pk, clientPk, localStatus, data } = record;
        const envelope = await crypto.encryptPayload(data, _aad("events", pk));
        try {
            await db.events.put({
                pk: pk,
                clientPk: clientPk || "",
                // Refs #1476 (R9): occurredAt NICHT plaintext ablegen — der
                // Zeitstempel liegt im verschluesselten `data`-Envelope
                // (`data.occurredAt`) und wird von dort gerendert.
                localStatus: localStatus || "modified",
                data: envelope,
            });
        } catch (e) {
            // Refs #2060: Bis hierher stand das `put` NACKT da — ohne jedes
            // try/catch, waehrend die Schwesterfunktion `saveClientBundle`
            // (offline-bundles.js:150-158) Quota-Fehler laengst auf einen
            // stabilen Vertrag normalisiert. Dieselbe Ursache — volle Platte —
            // kam damit bei der Offline-Mitnahme als klare Meldung an und beim
            // Speichern einer Offline-Bearbeitung als roher Dexie-Fehler, den
            // die Aufrufer (offline-edit.js) ungeprueft weiterreichten. Der
            // Vertrag ist hier gespiegelt; alles andere geht unveraendert
            // durch.
            if (_isQuotaError(e)) {
                const quotaErr = new Error("OfflineQuotaExceeded");
                quotaErr.name = "QuotaExceededError";
                throw quotaErr;
            }
            throw e;
        }
    }

    async function getOfflineEvent(pk) {
        return getDecrypted("events", pk);
    }

    async function listModifiedEvents() {
        return listDecrypted("events", (r) => r.localStatus === "modified" || r.localStatus === "new");
    }

    async function listConflicts() {
        return listDecrypted("events", (r) => r.localStatus === "conflict");
    }

    async function countUnsyncedEvents() {
        // DEFENSE (#1329/#1324): Diese Liste MUSS mit der Status-Invariante
        // im Header synchron bleiben — fehlt hier ein unsynced-Status, purgt
        // der Idle-Wipe echte Arbeit.
        // Dexie where() on indexed field `localStatus`:
        return db.events
            .where("localStatus")
            .anyOf(["modified", "new", "conflict", "dead"])
            .count();
    }

    async function countConflictEvents() {
        return db.events.where("localStatus").equals("conflict").count();
    }

    /*
     * Refs #1351/#1385 (M8/Task 4): Analog `countConflictEvents` fuer
     * `localStatus:"dead"` — indizierte Zaehlung ohne Decrypt, damit Banner/
     * Offline-Home dead-Events mitzaehlen koennen (Konflikt-Banner-Zaehler
     * = conflict+dead), ohne jede Row zu entschluesseln.
     */
    async function countDeadEvents() {
        return db.events.where("localStatus").equals("dead").count();
    }

    /*
     * Refs #1324: True, wenn offline noch NICHT synchronisierte Arbeit vorliegt
     * — die generische Write-Queue ODER modifizierte/neue/konfligierende Events.
     * Der Idle-Wipe fragt das ab, um bei ungesyncter Arbeit nur zu LOCKEN
     * (Schluessel verwerfen) statt zu purgen: der verschluesselte Bestand
     * ueberlebt, Re-Login leitet denselben PBKDF2-Schluessel ab und macht ihn
     * wieder lesbar/abspielbar — sonst gingen offline erfasste Eintraege beim
     * 30-Min-Idle still verloren.
     */
    async function hasUnsyncedData() {
        if ((await db.queue.count()) > 0) return true;
        return (await countUnsyncedEvents()) > 0;
    }

    /*
     * Refs #1484 (Review-Fix): Gate fuer den Startup-Drain — zaehlt NUR
     * auto-replaybares Werk. Bewusst NICHT hasUnsyncedData(): das ist das
     * Idle-Wipe-Praedikat und zaehlt conflict/dead MIT (damit sie nicht
     * gepurgt werden) — als Drain-Gate liefe der Sync sonst auf JEDER
     * Navigation (Lock + Revalidierungs-Requests fuer bis zu 20 Personen),
     * solange ein einziger unaufgeloester Konflikt existiert. Kriterien
     * spiegeln die Replay-Selektion: Queue-Rows nicht conflict/dead und
     * retryAfter faellig (offline-queue.js), Events nur new/modified
     * (offline-edit.js).
     */
    async function hasReplayableWork() {
        const now = Date.now();
        const queueReady = await db.queue
            .filter(
                (r) =>
                    r.localStatus !== "conflict" &&
                    r.localStatus !== "dead" &&
                    (!r.retryAfter || r.retryAfter <= now)
            )
            .count();
        if (queueReady > 0) return true;
        const eventsReady = await db.events.where("localStatus").anyOf(["new", "modified"]).count();
        return eventsReady > 0;
    }

    async function updateEventLocalStatus(pk, status) {
        // Dexie's `update()` only touches the indexed fields; safe because
        // `localStatus` is an index and not inside the encrypted envelope.
        return db.events.update(pk, { localStatus: status });
    }

    /*
     * Remember the server-side state we received on a 409 so the merge UI
     * can render it without another round-trip. The state itself is stored
     * inside the encrypted envelope on the event record to keep the
     * at-rest contract consistent with the rest of the offline store.
     */
    async function saveConflictState(pk, serverState) {
        const crypto = _crypto();
        const existing = await db.events.get(pk);
        if (!existing) {
            throw new Error("NoEventToMarkConflict:" + pk);
        }
        let envelope;
        try {
            envelope = await crypto.decryptPayload(existing.data, _aad("events", existing.pk));
        } catch (e) {
            if (_isTransientDecryptError(e)) {
                // Refs #1352: kein Schluessel geladen — Row unangetastet
                // lassen und den Fehler weiterreichen. Der Replay-Aufrufer
                // (nach Idle-Lock ohne Re-Login) sieht den 409 nach dem
                // naechsten Versuch mit gueltigem Schluessel erneut und legt
                // den Konflikt dann an.
                throw e;
            }
            // Key rotated / tampered row — drop it instead of persisting
            // an undecryptable conflict record. Matches the auto-discard
            // contract from #576.
            await db.events.delete(pk);
            return;
        }
        envelope.serverState = serverState;
        await db.events.put({
            ...existing,
            localStatus: "conflict",
            data: await crypto.encryptPayload(envelope, _aad("events", existing.pk)),
        });
    }

    async function clearOfflineEdit(pk) {
        // After a successful replay or an explicit "discard local" the row
        // must be removed so it is not re-played again.
        return db.events.delete(pk);
    }

    /* ─── dead-Letter fuer Event-Replays (Refs #1351/#1384) ──────────────── */

    /*
     * Ein Event nach einem PERMANENTEN Replay-Fehler (404/410 Edit-Ziel weg,
     * dauerhaft ungueltige Formulardaten, o.ae.) auf `localStatus: "dead"`
     * setzen statt es endlos erneut zu versuchen. `dead` ist laut
     * Kern-Invariante (S1) KEIN Loeschen — die Row bleibt bis zu einer
     * expliziten Nutzeraktion (`retryDeadEvent`/`discardDeadEvent`, M8)
     * erhalten. `deadReason`/`lastError`/`lastAttemptAt` landen im
     * verschluesselten Envelope (nicht indiziert, keine Suche noetig);
     * `wasNew` merkt sich, ob das Event serverseitig NIE existierte (fuer
     * `retryDeadEvent`: Retry ueber /events/new/ statt /edit/).
     */
    async function markEventDead(pk, reason, lastError) {
        const crypto = _crypto();
        const existing = await db.events.get(pk);
        if (!existing) return; // Race mit einer anderen Aktion — nichts zu markieren.
        let envelope;
        try {
            envelope = await crypto.decryptPayload(existing.data, _aad("events", existing.pk));
        } catch (e) {
            if (_isTransientDecryptError(e)) {
                // Refs #1352: kein Schluessel geladen — Row unangetastet
                // lassen, der naechste Versuch mit gueltigem Schluessel
                // klassifiziert erneut statt jetzt blind "dead" ohne
                // lesbaren Envelope zu schreiben.
                return;
            }
            // Permanent unentschluesselbar -> Auto-Discard-Konvention (#576/F-03).
            await db.events.delete(pk);
            return;
        }
        envelope.deadReason = reason;
        envelope.lastError = lastError || "";
        envelope.lastAttemptAt = Date.now();
        if (existing.localStatus === "new") {
            envelope.wasNew = true;
        }
        await db.events.put({
            ...existing,
            localStatus: "dead",
            data: await crypto.encryptPayload(envelope, _aad("events", existing.pk)),
        });
    }

    async function listDeadEvents() {
        return listDecrypted("events", (r) => r.localStatus === "dead");
    }

    /*
     * Refs #1351/#1384 (M8/Task 4): Nutzeraktion "Erneut versuchen" fuer ein
     * dead Event. Ging das Event serverseitig NIE existieren (Envelope-Flag
     * `wasNew`) und starb es aus einem Grund, der auf eine reparable
     * Eingabe hindeutet (`invalid`/`unexpected-response`), geht es zurueck
     * auf "new" — der naechste Replay laeuft ueber /events/new/. Alle
     * anderen Faelle (insbesondere `not-found`/`forbidden`, oder ein Event,
     * das serverseitig bereits existierte) gehen auf "modified" zurueck
     * (naechster Replay ueber /events/<pk>/edit/).
     */
    async function retryDeadEvent(pk) {
        const crypto = _crypto();
        const existing = await db.events.get(pk);
        if (!existing) throw new Error("NoEventToRetry:" + pk);
        let envelope;
        try {
            envelope = await crypto.decryptPayload(existing.data, _aad("events", existing.pk));
        } catch (e) {
            if (_isTransientDecryptError(e)) throw e;
            await db.events.delete(pk);
            return null;
        }
        const backToNew =
            envelope.wasNew === true &&
            (envelope.deadReason === "invalid" || envelope.deadReason === "unexpected-response");
        const nextStatus = backToNew ? "new" : "modified";
        delete envelope.deadReason;
        await db.events.put({
            ...existing,
            localStatus: nextStatus,
            data: await crypto.encryptPayload(envelope, _aad("events", existing.pk)),
        });
        return nextStatus;
    }

    async function discardDeadEvent(pk) {
        // Nutzeraktion (S1-Ausnahme 1) — Pendant zu clearOfflineEdit fuer
        // den dead-Letter-Pfad.
        return clearOfflineEdit(pk);
    }

    /* ─── generische Queue: Listing + Nutzeraktionen (Refs #1351/#1384) ──── */

    function _isTallyQueueUrl(url) {
        const patterns = (typeof self !== "undefined" && self.URL_PATTERNS) || null;
        if (!patterns || !patterns.TALLY_INCREMENT) return false;
        try {
            const parsed = new URL(url, window.location.origin);
            return (
                parsed.origin === window.location.origin &&
                patterns.TALLY_INCREMENT.test(parsed.pathname)
            );
        } catch (_e) {
            return false;
        }
    }

    const _CANONICAL_UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

    /*
     * #1912: Schmale, wertfreie UI-Sicht auf Tally-Queue-Eintraege. Quelle ist
     * ausschliesslich die bestehende verschluesselte Queue; Body und
     * Zahlenwerte werden nur zum Gruppieren entschluesselt und nie
     * zurueckgegeben. Permanenter Key-Verlust bleibt ueber den bereits
     * vorhandenen dead-Tombstone als unzuordenbarer Blocker sichtbar.
     */
    async function listTallyQueueState() {
        const rows = await listDecrypted("queue");
        // Null-Prototyp verhindert, dass selbst ein manipulierter Queue-Body
        // Property-Pfade wie __proto__ oder constructor erreichen kann.
        const byDocumentType = Object.create(null);
        let pending = 0;
        let blocked = 0;
        let unassignedPending = 0;
        let unassignedBlocked = 0;
        const decryptedIds = new Set();

        for (const row of rows) {
            if (!_isTallyQueueUrl(row.url || "")) continue;
            decryptedIds.add(row.id);
            let documentType = null;
            try {
                const params = new URLSearchParams((row.data && row.data.body) || "");
                const candidate = params.get("document_type");
                documentType = candidate && _CANONICAL_UUID_RE.test(candidate) ? candidate : null;
            } catch (_e) {
                /* kaputter Body bleibt als unzuordenbarer Queue-Zustand sichtbar */
            }
            const isBlocked = row.localStatus === "conflict" || row.localStatus === "dead";
            if (isBlocked) blocked += 1;
            else pending += 1;
            if (!documentType) {
                if (isBlocked) unassignedBlocked += 1;
                else unassignedPending += 1;
                continue;
            }
            const state = byDocumentType[documentType] || { pending: 0, blocked: 0 };
            if (isBlocked) state.blocked += 1;
            else state.pending += 1;
            byDocumentType[documentType] = state;
        }

        // ``listDecrypted`` tombstoned permanent unlesbare Queue-Rows, gibt
        // deren Body aber absichtlich nicht zurueck. Die URL ist bereits ein
        // bestehendes unverschluesseltes Queue-Indexfeld und reicht, um sie als
        // Tally-Blocker sichtbar zu halten — ohne Werte oder Typ-ID auszugeben.
        const rawRows = await db.queue.toArray();
        for (const row of rawRows) {
            if (decryptedIds.has(row.id) || !_isTallyQueueUrl(row.url || "")) continue;
            if (row.localStatus === "conflict" || row.localStatus === "dead") {
                blocked += 1;
                unassignedBlocked += 1;
            }
        }
        return {
            byDocumentType: byDocumentType,
            pending: pending,
            blocked: blocked,
            unassignedPending: unassignedPending,
            unassignedBlocked: unassignedBlocked,
        };
    }

    /*
     * Duenne, UI-taugliche Sicht auf die Queue fuer die M8-Dead-Letter-UI
     * (Task 4). `method` kommt aus dem verschluesselten `data`-Feld (muss
     * daher entschluesselt werden) — Body-Inhalte werden bewusst NICHT
     * zurueckgegeben (koennten PII tragen).
     */
    async function listQueueEntries() {
        const rows = await listDecrypted("queue");
        return rows.map((r) => {
            const entry = {
                id: r.id,
                url: r.url,
                createdAt: r.createdAt,
                attempts: r.attempts || 0,
                lastError: r.lastError || "",
                localStatus: r.localStatus || null,
                method: (r.data && r.data.method) || "",
            };
            // Refs #1419/#1390/#1465: fuer JEDE 409-Row mit persistiertem
            // server_state (Status ODER Edit) eine fachliche Konflikt-Sicht
            // ableiten — damit die M8-Liste den Server-Stand rendern und per
            // "Erneut anwenden" (Token-Rewrite) aufloesen kann, nicht nur bei
            // WORKITEM_STATUS. Weiterhin KEIN Roh-Body an die UI; bei Status-
            // Rows zusaetzlich der Ziel-Status aus dem gequeuten Body.
            const patterns = (typeof self !== "undefined" && self.URL_PATTERNS) || null;
            const conflict = (r.data && r.data.conflict) || null;
            const serverState = (conflict && conflict.serverState) || null;
            if (serverState && serverState.updated_at) {
                const isStatus = !!(
                    patterns &&
                    patterns.WORKITEM_STATUS &&
                    patterns.matchesPath &&
                    patterns.matchesPath(patterns.WORKITEM_STATUS, r.url || "")
                );
                let intended = null;
                if (isStatus) {
                    try {
                        intended = new URLSearchParams((r.data && r.data.body) || "").get("status");
                    } catch (_e) {
                        /* Body nicht parsebar — generische Darstellung */
                    }
                }
                entry.conflictInfo = {
                    isStatus: isStatus,
                    intendedStatus: intended,
                    serverState: serverState,
                    conflictError: (conflict && conflict.error) || null,
                };
            }
            return entry;
        });
    }

    /*
     * Nutzeraktion "Erneut versuchen" fuer eine conflict/dead Queue-Row:
     * wieder fuer den naechsten Auto-Replay freigeben. `url`/`retryAfter`/
     * `attempts`/`localStatus` sind unverschluesselte Row-Felder — kein
     * Decrypt/Re-Encrypt noetig.
     */
    async function retryQueueEntry(id) {
        return db.queue.update(id, { localStatus: null, retryAfter: 0, attempts: 0 });
    }

    async function discardQueueEntry(id) {
        // Nutzeraktion (S1-Ausnahme 1) → delete erlaubt.
        return db.queue.delete(id);
    }

    /*
     * Nutzeraktion "Erneut anwenden" fuer einen Status-Konflikt (Refs #1419):
     * uebernimmt das updated_at des beim 409 persistierten Server-Stands als
     * frisches expected_updated_at in den gequeuten Body und gibt die Row
     * wieder fuer den Auto-Replay frei. Der naechste Replay wendet die Aktion
     * damit gegen exakt den Stand an, den die Nutzer:in im Konflikt-Dialog
     * GESEHEN hat — aendert sich der Server zwischenzeitlich erneut, kommt
     * wieder ein 409 (kein blindes LWW). Body-Rewrite erfordert Decrypt +
     * Re-Encrypt (anders als retryQueueEntry, das nur Row-Metadaten anfasst).
     */
    async function reapplyQueueEntryWithServerVersion(id) {
        const row = await getDecrypted("queue", id);
        if (!row) {
            throw new Error("QueueRowNotFound");
        }
        const conflict = (row.data && row.data.conflict) || null;
        const serverState = (conflict && conflict.serverState) || null;
        if (!serverState || !serverState.updated_at) {
            throw new Error("NoServerState");
        }
        const params = new URLSearchParams((row.data && row.data.body) || "");
        params.set("expected_updated_at", serverState.updated_at);
        const data = { ...row.data, body: params.toString() };
        delete data.conflict;
        return putEncrypted("queue", {
            ...row,
            data: data,
            localStatus: null,
            retryAfter: 0,
            attempts: 0,
            lastError: "",
        });
    }

    /*
     * Refs #1466: Coalescing eines WORKITEM_STATUS-POST auf dieselbe pk (URL).
     * Liegt bereits eine replaybare (nicht conflict/dead) Queue-Row fuer die
     * URL vor, wird deren Body durch den neuen (finalen) Intent ersetzt —
     * Last-Write-Wins des Offline-Intents: EIN eingefrorener Token T0, EIN
     * Idempotenz-Key. So erzeugen eine legitime Progression
     * (open->in_progress->done) oder ein Doppelklick keinen
     * Phantom-Selbstkonflikt beim Replay. Etwaige Alt-Duplikate derselben URL
     * werden dabei mit eingesammelt. Rueckgabe: true, wenn coalesced wurde
     * (Aufrufer legt dann NICHT neu an), sonst false.
     */
    async function coalescePendingQueueByUrl(url, method, body, headers) {
        const all = await db.queue.toArray();
        const pending = all
            .filter((r) => r.url === url && r.localStatus !== "conflict" && r.localStatus !== "dead")
            .sort((a, b) => a.id - b.id);
        if (pending.length === 0) return false;
        const keep = await getDecrypted("queue", pending[0].id);
        if (!keep) return false;
        const data = { ...(keep.data || {}), method: method, body: body, headers: headers || {} };
        // Ein etwaiger frueherer 409-Server-Stand ist mit dem neuen Intent hinfaellig.
        delete data.conflict;
        await putEncrypted("queue", {
            ...keep,
            data: data,
            localStatus: null,
            retryAfter: 0,
            attempts: 0,
            lastError: "",
        });
        for (let i = 1; i < pending.length; i += 1) {
            await db.queue.delete(pending[i].id);
        }
        return true;
    }

    /*
     * `pending`/`blocked`-Aufteilung fuers Queue-Badge OHNE Decrypt:
     * `localStatus`/`retryAfter` sind unverschluesselte Row-Felder (siehe
     * `putEncrypted`), die Aufteilung funktioniert daher auch ohne
     * Session-Key (z.B. vor dem ersten Login-Handshake nach einem Boot).
     */
    async function countQueueByStatus() {
        const rows = await db.queue.toArray();
        let blocked = 0;
        for (const row of rows) {
            if (row.localStatus === "conflict" || row.localStatus === "dead") blocked += 1;
        }
        return { total: rows.length, pending: rows.length - blocked, blocked: blocked };
    }

    /*
     * Refs #1356: Frage den Browser EINMALIG um dauerhaften Speicher
     * (navigator.storage.persist()). Ohne diesen Grant darf der Browser die
     * Origin-IndexedDB unter Speicherdruck evicten (Safari-ITP zusaetzlich
     * zeitbasiert bei nicht installierter PWA) — inklusive verschluesselter
     * Bundles UND ungesyncter Edits/Queue-Rows. Das Ergebnis wird in
     * `meta` gecacht, damit der Browser-Prompt nicht bei jedem weiteren
     * Take/Edit erneut auftaucht.
     *
     * Rueckgabe:
     *   true/false — Browser-Antwort (aus dem Cache oder frisch erfragt).
     *   null       — kein `navigator.storage.persist` (Feature-Detection)
     *                ODER der Call ist fehlgeschlagen. Bewusst NICHT
     *                gecacht: ein spaeterer Aufruf (z.B. nach einem
     *                Browser-Update) soll erneut fragen duerfen statt an
     *                einem verworfenen `null` haengen zu bleiben.
     */
    async function ensurePersistentStorage() {
        const cached = await db.meta.get("storagePersist");
        if (cached) return cached.granted;

        if (!navigator.storage || !navigator.storage.persist) {
            return null;
        }

        let granted;
        try {
            granted = await navigator.storage.persist();
        } catch (_e) {
            return null;
        }

        // Bewusst UNverschluesselt: kein PII, nur ein Boolean-Flag zum
        // Re-Prompt-Schutz — dafuer lohnt sich kein Crypto-Overhead.
        await db.meta.put({ key: "storagePersist", granted: granted, ts: Date.now() });
        return granted;
    }

    /*
     * Refs #1412 (M17b): reiner Live-Wrapper um navigator.storage.estimate()
     * fuer die Quota-/Belegungsanzeige im Offline-Arbeitsplatz. KEIN Caching
     * (im Gegensatz zu ensurePersistentStorage) — die Belegung aendert sich
     * laufend, ein gecachter Wert waere sofort veraltet. Fail-soft wie der
     * Persist-Pfad: Feature fehlt oder der Call wirft => null, blockiert
     * nichts (der Aufrufer zeigt dann einfach kein Quota-Badge).
     */
    async function getStorageEstimate() {
        if (!navigator.storage || !navigator.storage.estimate) {
            return null;
        }
        try {
            const estimate = await navigator.storage.estimate();
            const usage = estimate && estimate.usage;
            const quota = estimate && estimate.quota;
            if (typeof usage !== "number" || typeof quota !== "number" || quota <= 0) {
                return null;
            }
            // Refs #1546 (AC3): Aufschluesselung CacheStorage vs. IndexedDB,
            // damit offline-Speicherwachstum intern nachvollziehbar wird (der
            // SW-Runtime-Cache war der stille Treiber, ~245 MB). Beide Felder
            // sind additiv und fail-soft: schlaegt die Ermittlung fehl, bleibt
            // das jeweilige Feld null — die Gesamt-Belegung (usage/quota/
            // percent) und alle bestehenden Aufrufer bleiben unberuehrt.
            const cacheStorageBytes = await _measureCacheStorageBytes();
            let indexedDbBytes = null;
            // Bevorzugt der praezise Wert aus estimate().usageDetails (Chromium
            // liefert ihn), sonst eine Naeherung aus Gesamt-Belegung minus
            // gemessenem CacheStorage (nie negativ; enthaelt neben IndexedDB
            // auch andere Quoten-Posten, daher nur als Fallback markiert).
            const details = estimate.usageDetails;
            if (details && typeof details.indexedDB === "number") {
                indexedDbBytes = details.indexedDB;
            } else if (typeof cacheStorageBytes === "number") {
                indexedDbBytes = Math.max(0, usage - cacheStorageBytes);
            }
            return {
                usage: usage,
                quota: quota,
                percent: Math.round((usage / quota) * 100),
                cacheStorageBytes: cacheStorageBytes,
                indexedDbBytes: indexedDbBytes,
            };
        } catch (_e) {
            return null;
        }
    }

    /*
     * Refs #1546 (AC3): Summiert die belegten Bytes ueber ALLE
     * CacheStorage-Eintraege (SW-Precache + Runtime-Cache). Pro Eintrag zuerst
     * der Content-Length-Header, sonst die Blob-Groesse des geklonten Response.
     * Jeder Einzelschritt ist defensiv gekapselt: ein einzelner unlesbarer
     * Eintrag (opaque Response, verworfener Body) darf die Gesamt-Messung nicht
     * kippen. Rueckgabe:
     *   number — Summe in Bytes (0, wenn keine Caches vorhanden).
     *   null   — CacheStorage fehlt (kein Secure Context / alter Browser) oder
     *            die Iteration scheitert komplett; der Aufrufer behandelt null
     *            als "unbekannt" und faellt auf die Differenz-Naeherung zurueck.
     */
    async function _measureCacheStorageBytes() {
        const cacheStorage = typeof self !== "undefined" ? self.caches : null;
        if (!cacheStorage || typeof cacheStorage.keys !== "function") {
            return null;
        }
        try {
            let total = 0;
            const names = await cacheStorage.keys();
            for (const name of names) {
                let cache;
                try {
                    cache = await cacheStorage.open(name);
                } catch (_e) {
                    continue;
                }
                const requests = await cache.keys();
                for (const request of requests) {
                    try {
                        const response = await cache.match(request);
                        if (!response) {
                            continue;
                        }
                        const len = response.headers.get("content-length");
                        if (len && Number.isFinite(Number(len))) {
                            total += Number(len);
                        } else {
                            const blob = await response.clone().blob();
                            total += blob.size;
                        }
                    } catch (_e) {
                        // einzelner Eintrag unlesbar -> ueberspringen
                    }
                }
            }
            return total;
        } catch (_e) {
            return null;
        }
    }

    /*
     * Refs #1412 (M17b): liefert den Persist-Grant-Status fuer die Anzeige im
     * Offline-Arbeitsplatz. Reiner Cache-Read aus `db.meta` ("storagePersist",
     * derselbe Key wie ensurePersistentStorage) — fragt NIE selbst
     * navigator.storage.persist(), das bleibt takeClientOffline vorbehalten
     * (kein ungewollter Browser-Prompt nur weil der Nutzer die Anzeige
     * ansieht).
     *
     * Rueckgabe:
     *   "granted"/"denied" — aus dem Cache (Feld `granted`).
     *   "unsupported"      — navigator.storage.persist fehlt in diesem Browser.
     *   null                — Feature vorhanden, aber noch nie gefragt (kein
     *                         Take/Edit bisher lief). Bewusst KEIN drittes
     *                         Wort dafuer: der Aufrufer zeigt dann kein Badge,
     *                         statt faelschlich "nicht unterstuetzt" zu
     *                         behaupten.
     */
    async function getPersistStatus() {
        if (!navigator.storage || !navigator.storage.persist) {
            return "unsupported";
        }
        const cached = await db.meta.get("storagePersist");
        if (!cached) {
            return null;
        }
        return cached.granted ? "granted" : "denied";
    }

    // Refs #1838: EINE Fassade — offline-db.js legt sie an, jede Datei des
    // Verbunds haengt ihre Exporte per Object.assign daran (fuer die 12
    // Konsumenten unveraendert window.offlineStore).
    window.offlineStore = Object.assign(window.offlineStore || {}, {
        // Stage 3 (#575) — offline edit + conflict tracking
        saveOfflineEdit: saveOfflineEdit,
        getOfflineEvent: getOfflineEvent,
        listModifiedEvents: listModifiedEvents,
        listConflicts: listConflicts,
        countUnsyncedEvents: countUnsyncedEvents,
        countConflictEvents: countConflictEvents,
        // Refs #1351/#1385 — M8/Task 4: dead-Zaehler + Toggle-Warnung
        countDeadEvents: countDeadEvents,
        hasUnsyncedData: hasUnsyncedData,
        hasReplayableWork: hasReplayableWork,
        updateEventLocalStatus: updateEventLocalStatus,
        saveConflictState: saveConflictState,
        clearOfflineEdit: clearOfflineEdit,
        // Refs #1351/#1384 — dead-Letter fuer Event-Replays (M8/Task 4)
        markEventDead: markEventDead,
        listDeadEvents: listDeadEvents,
        retryDeadEvent: retryDeadEvent,
        discardDeadEvent: discardDeadEvent,
        // Refs #1351/#1384 — generische Queue: Listing + Nutzeraktionen (M8/Task 4)
        listQueueEntries: listQueueEntries,
        listTallyQueueState: listTallyQueueState,
        coalescePendingQueueByUrl: coalescePendingQueueByUrl,
        retryQueueEntry: retryQueueEntry,
        reapplyQueueEntryWithServerVersion: reapplyQueueEntryWithServerVersion,
        discardQueueEntry: discardQueueEntry,
        countQueueByStatus: countQueueByStatus,
        // Refs #1356 — persistenter Speicher (Eviction-Schutz)
        ensurePersistentStorage: ensurePersistentStorage,
        // Refs #1412 (M17b) — Quota-/Persist-Status-Anzeige
        getStorageEstimate: getStorageEstimate,
        getPersistStatus: getPersistStatus,
    });

    /*
     * F-04 + F-10 (#1110): Sobald die Verbindung zurueckkehrt, zuerst die
     * abgelaufenen Bundles per TTL verwerfen (rein lokal, ohne Schluessel-
     * Risiko) und anschliessend die verbliebenen gegen den Server
     * re-validieren. Eigener Listener in dieser Datei — koexistiert mit den
     * `online`-Listenern der Queue/Edit-Module. Fehler werden geschluckt,
     * damit ein Re-Validierungs-Problem den uebrigen Online-Sync nicht stoert.
     */
    if (typeof window !== "undefined" && window.addEventListener) {
        window.addEventListener("online", async () => {
            // M6 (Refs #1351/#1383): Ist der Sync-Orchestrator geladen (base.html),
            // koordiniert dessen requestSync-Sequenz purgeExpired+revalidate hinter
            // dem origin-weiten Web Lock — hier nichts tun (sonst liefe die
            // Re-Validierung doppelt/unkoordiniert). Nur als Fallback auf Seiten
            // OHNE Orchestrator (offline-Shell/Login) direkt re-validieren wie bisher.
            if (window.syncOrchestrator && window.syncOrchestrator.requestSync) return;
            try {
                // Refs #1352: ready() VOR hasSessionKey() abwarten — sonst
                // liefert die synchrone Cache-Pruefung direkt nach einem
                // frischen Seiten-Load ein falsches Negativ (initialLoad ist
                // noch nicht durchgelaufen) und das Key-Gate greift faelschlich.
                const cs = window.crypto_session;
                await cs?.awaitReady?.();
                const hasKey = cs && cs.hasSessionKey ? cs.hasSessionKey() : false;
                // Refs #1352: ohne Schluessel keine Loeschentscheidung — das
                // Gate umschliesst jetzt auch purgeExpired(), nicht nur die
                // Re-Validierung. Ohne Key ist die Session ohnehin idle-
                // gelockt (#1324); der naechste Online-Kontakt mit
                // gueltigem Schluessel holt beides nach.
                if (hasKey) {
                    await purgeExpired(Date.now());
                    // Refs #1760: auch im Orchestrator-losen Fallback zuerst die
                    // Widerrufe anwenden — sonst haette eine Offline-Shell
                    // (ohne base.html) keinen Zugang zum Signal und zeigte eine
                    // anonymisierte Person bis zum TTL-Ablauf weiter.
                    // Refs #1838 (Review-Befund, critical): beide Funktionen
                    // leben seit dem Split in Schwester-Modulen — als freie
                    // Bezeichner waeren sie hier ein ReferenceError, den der
                    // try/catch STILL schluckte (Art.-17-Purge tot auf den
                    // Seiten ohne Orchestrator). Aufloesung zur Callzeit ueber
                    // die Fassade; gepinnt in offline-store-split.test.mjs.
                    await window.offlineStore.applyClientRevocations();
                    await window.offlineStore.revalidateCachedClients();
                }
            } catch (_e) {
                // eslint-disable-next-line no-console
                console.debug("[offline-store] online-revalidation skipped");
            }
        });
    }

    /*
     * Refs #1412 (M17b, Design-Entscheidung 4): minimaler, gebundener
     * Re-Prompt nach PWA-Installation. Installierte PWAs bekommen von den
     * meisten Browsern grosszuegiger persistenten Speicher gewaehrt als
     * Tab-Kontexte — ein Grant, der VOR der Installation verweigert wurde,
     * lohnt daher einen erneuten Versuch. Kein sofortiger Prompt hier (kein
     * Browser-Dialog ausserhalb des etablierten Take-Flows): nur die
     * Cache-Invalidierung, der naechste ensurePersistentStorage()-Aufruf
     * (naechste Mitnahme/Edit) darf danach erneut fragen. offline-store.js
     * ist auf jeder Seite geladen, die einen Install-Prompt zeigen kann
     * (Login UND base.html) — der passende, bereits geladene Ort dafuer.
     */
    if (typeof window !== "undefined" && window.addEventListener) {
        window.addEventListener("appinstalled", () => {
            db.meta.delete("storagePersist").catch(function () {
                // Nicht fatal — der naechste Take fragt im Zweifel einfach
                // aus dem (dann noch bestehenden) Cache erneut nicht.
            });
        });
    }

    // Refs #1838 (Review-Befund, major): Faellt ein MITTLERES Modul des
    // Verbunds aus (Einzel-Netzfehler, Blocker-Filter, korrupter
    // Cache-Eintrag ausserhalb des SW-Precache), laufen take()-Budget und
    // Fail-closed-Guards trotzdem still durch — die Fassade fehlte dann
    // lautlos um dessen Exporte. Als letzte Datei der Kette prueft dieses
    // Modul deshalb je Schwester einen Anker-Export und meldet den Ausfall
    // HOERBAR (kein Abbruch: die uebrigen Teile bleiben nutzbar, aber die
    // Ursache steht in der Konsole statt als spaeter TypeError irgendwo tief
    // im Aufrufpfad).
    for (const [sibling, probe] of [
        ["offline-bundles.js", "saveClientBundle"],
        ["offline-revocations.js", "applyClientRevocations"],
    ]) {
        if (typeof window.offlineStore[probe] !== "function") {
            // eslint-disable-next-line no-console
            console.error(
                "[offline-store] " + sibling + " hat nicht geladen — Fassade unvollstaendig (Refs #1838)."
            );
        }
    }
})();
