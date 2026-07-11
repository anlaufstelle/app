/*
 * Encrypted-at-rest IndexedDB store for the offline mode.
 *
 * Wraps Dexie.js with a thin envelope that runs every payload through
 * window.crypto_session.encryptPayload before write and decryptPayload after
 * read. A PERMANENT decrypt failure (salt rotated, password changed) drops
 * the offending row silently — the auto-discard behaviour specified for
 * #573 / #576. A TRANSIENT failure (no key loaded — Idle-Lock #1324, or a
 * fresh boot before re-login) leaves the row untouched instead (Refs #1352,
 * see `_isTransientDecryptError`).
 *
 * Unsynced work — `events` rows with `localStatus` in
 * {modified, new, conflict, dead} and every `queue` row — is never dropped
 * silently by TTL expiry or a client re-take. Only an explicit user action
 * (P1/M8), the security purge on access revocation
 * (`revalidateCachedClient` at 404/410, F-10/#1110), `purgeAll` (logout) or
 * the permanent-decrypt-discard above may remove it. See
 * `removeOfflineClient`'s `force` parameter (Refs #1353).
 *
 * Schema v1 (Stage 1, #576):
 *   queue:  ++id, url, createdAt, lastError, retryAfter, attempts
 *   drafts: formKey, updatedAt
 *   meta:   key
 *
 * Schema v2 (Stage 2, #574):
 *   clients: pk, lastSynced
 *   cases:   pk, clientPk, lastSynced
 *   events:  pk, clientPk, occurredAt, localStatus
 * Every record's `data` field is { iv, ct } (never plaintext).
 *
 * Schema v3 (Refs #1351/#1384): `queue` gains an indexed `localStatus`
 * (`null | "conflict" | "dead"`) so the generic replay queue can exclude
 * conflicting/dead-lettered rows from auto-replay the same way `events`
 * already does. `lastError`/`attempts` stay plain (unindexed) row fields —
 * Dexie stores them regardless of whether they're listed in the index
 * string, only `.where()` queries need the index.
 *   queue: ++id, url, createdAt, retryAfter, localStatus
 */
(function () {
    "use strict";

    const DB_NAME = "anlaufstelle-offline";
    // SI-2 (#1520/#1499): `facility` ist Teil von TABLES, damit purgeAll
    // (Logout/Passwortwechsel) das personenlose Meta-Bundle mitloescht.
    const TABLES = ["queue", "drafts", "meta", "clients", "cases", "events", "facility"];
    const TTL_MS = 48 * 60 * 60 * 1000; // 48h
    const MAX_OFFLINE_CLIENTS = 20;
    // F-05 (#1425, ADR-022): muss mit BUNDLE_SCHEMA_VERSION in
    // core/services/system/offline.py synchron gehalten werden (kein
    // Build-Sync, analog TTL_MS/BUNDLE_TTL_SECONDS oben) -- ein Mismatch
    // zwingt den Lesepfad zum Purge, siehe _isSchemaMismatch.
    const BUNDLE_SCHEMA_VERSION = 1;
    // SI-2 (#1520/#1499): eigene Schema-Version fuers personenlose
    // Facility-Meta-Bundle -- UNABHAENGIG von BUNDLE_SCHEMA_VERSION (Klient),
    // damit beide Bundles getrennt evolvieren koennen. Muss mit
    // FACILITY_BUNDLE_SCHEMA_VERSION in core/services/system/offline.py
    // synchron bleiben (kein Build-Sync, wie BUNDLE_SCHEMA_VERSION). Ein
    // Mismatch zwingt den Lesepfad zum Purge, siehe _isFacilitySchemaMismatch.
    const FACILITY_BUNDLE_SCHEMA_VERSION = 1;
    // SI-2 (#1520/#1499): das Facility-Bundle ist ein Singleton (genau eine
    // Facility pro Session) -> fixer Primaerkey, kein pk-Fan-out.
    const FACILITY_ROW_KEY = "self";

    if (typeof Dexie === "undefined") {
        // eslint-disable-next-line no-console
        console.error("[offline-store] Dexie not loaded — offline mode disabled");
        return;
    }

    const db = new Dexie(DB_NAME);
    db.version(1).stores({
        queue: "++id, url, createdAt, lastError, retryAfter, attempts",
        drafts: "formKey, updatedAt",
        meta: "key",
    });
    db.version(2).stores({
        queue: "++id, url, createdAt, lastError, retryAfter, attempts",
        drafts: "formKey, updatedAt",
        meta: "key",
        clients: "pk, lastSynced",
        cases: "pk, clientPk, lastSynced",
        events: "pk, clientPk, occurredAt, localStatus",
    });
    // Refs #1351/#1384: additiver Upgrade — bestehende Rows behalten
    // `localStatus` undefined (= aktiv/pending), niemand wird beim Upgrade
    // umgeschrieben oder verworfen.
    db.version(3).stores({
        queue: "++id, url, createdAt, retryAfter, localStatus",
        drafts: "formKey, updatedAt",
        meta: "key",
        clients: "pk, lastSynced",
        cases: "pk, clientPk, lastSynced",
        events: "pk, clientPk, occurredAt, localStatus",
    });
    // SI-2 (#1520/#1499): additiver Upgrade — neue `facility`-Tabelle fuers
    // personenlose Offline-Create-Meta-Bundle (Singleton, Primaerkey "key",
    // fixer Wert "self"). Dexie verlangt die vollstaendige Wiederholung ALLER
    // Tabellen der Vorversion — v3-Tabellen unveraendert uebernommen, niemand
    // wird beim Upgrade umgeschrieben oder verworfen.
    db.version(4).stores({
        queue: "++id, url, createdAt, retryAfter, localStatus",
        drafts: "formKey, updatedAt",
        meta: "key",
        clients: "pk, lastSynced",
        cases: "pk, clientPk, lastSynced",
        events: "pk, clientPk, occurredAt, localStatus",
        facility: "key",
    });

    function _crypto() {
        if (!window.crypto_session) {
            throw new Error("CryptoSessionNotLoaded");
        }
        return window.crypto_session;
    }

    /*
     * Refs #1352: Unterscheidet einen TRANSIENTEN Decrypt-Fehler (kein
     * Schluessel geladen — Idle-Lock #1324 oder frischer Boot vor Re-Login)
     * von einem PERMANENTEN (Salt rotiert/Passwort gewechselt, kaputter
     * Datensatz). Nur bei PERMANENT bleibt der Auto-Discard-Mechanismus
     * (#576/F-03) aktiv; bei TRANSIENT darf keine der Discard-Stellen unten
     * die Row anfassen — sie ist mit dem naechsten gueltigen Schluessel
     * wieder lesbar.
     */
    function _isTransientDecryptError(e) {
        return e && (e.name === "NoSessionKeyError" || e.message === "NoSessionKey");
    }

    // Refs R1 (Sicherheitsreview 2026-07-05): Ungesyncte Arbeit — jede
    // `queue`-Row und `events` mit `localStatus` in {new, modified, conflict,
    // dead}. Nur diese darf bei einem PERMANENTEN Decrypt-Fehler nicht STILL
    // verschwinden; rein gecachte, serverseitig erneut abrufbare Read-Kopien
    // (clients/cases/drafts/synced events) bleiben beim #576-Auto-Discard.
    const _UNSYNCED_EVENT_STATUS = ["new", "modified", "conflict", "dead"];

    function _isUnsyncedWorkRow(table, row) {
        if (table === "queue") return true;
        if (table === "events") return _UNSYNCED_EVENT_STATUS.includes(row && row.localStatus);
        return false;
    }

    async function _discardOrTombstone(table, primaryKey, row) {
        // R1: Salt-Rotation (Rollenwechsel/Deaktivierung) bzw. Passwortwechsel
        // macht die Zeile unentschluesselbar — der Klartext ist ohne den alten
        // Schluessel unwiederbringlich. Ungesyncte Arbeit wird deshalb NICHT
        // mehr still geloescht, sondern als sichtbarer dead-Tombstone markiert
        // (planes Index-Update, kein Decrypt/Re-Encrypt noetig), damit die
        // vorhandene Sync-/Konflikt-/Dead-Letter-Anzeige (#1385) den Verlust
        // ausweist statt ihn zu verschlucken. Idempotent: eine bereits
        // getombstonete Zeile wird nicht erneut geschrieben.
        if (_isUnsyncedWorkRow(table, row)) {
            if (row && row.localStatus === "dead" && row.keyRotated) return;
            await db[table].update(primaryKey, { localStatus: "dead", keyRotated: true });
            return;
        }
        await db[table].delete(primaryKey);
    }

    async function putEncrypted(table, record) {
        const crypto = _crypto();
        const { data, ...rest } = record;
        const envelope = await crypto.encryptPayload(data);
        return db[table].put({ ...rest, data: envelope });
    }

    async function getDecrypted(table, primaryKey) {
        const crypto = _crypto();
        const row = await db[table].get(primaryKey);
        if (!row) return null;
        try {
            row.data = await crypto.decryptPayload(row.data);
            return row;
        } catch (e) {
            if (_isTransientDecryptError(e)) {
                // Refs #1352: kein Schluessel geladen — TRANSIENT, Row
                // behalten und nur null liefern.
                return null;
            }
            // PERMANENT decrypt failure (salt/password rotated): discard a
            // re-fetchable cache row, but tombstone unsynced work so it is
            // surfaced, not silently lost (Refs R1).
            await _discardOrTombstone(table, primaryKey, row);
            return null;
        }
    }

    async function listDecrypted(table, filterFn) {
        const crypto = _crypto();
        const rows = await db[table].toArray();
        const out = [];
        for (const row of rows) {
            try {
                const decrypted = { ...row, data: await crypto.decryptPayload(row.data) };
                if (!filterFn || filterFn(decrypted)) {
                    out.push(decrypted);
                }
            } catch (e) {
                if (_isTransientDecryptError(e)) {
                    // Refs #1352: kein Schluessel — Row NICHT loeschen, nur
                    // in dieser Liste ueberspringen.
                    continue;
                }
                // PERMANENT decrypt failure: discard cache, tombstone unsynced
                // work so it is surfaced rather than silently lost (Refs R1).
                await _discardOrTombstone(table, row[db[table].schema.primKey.name], row);
            }
        }
        return out;
    }

    async function deleteRow(table, primaryKey) {
        return db[table].delete(primaryKey);
    }

    async function purgeAll() {
        await Promise.all(TABLES.map((t) => db[t].clear()));
    }

    async function purgeExpired(now) {
        const ts = now || Date.now();
        const cutoff = ts - TTL_MS;
        // Refs #1353: KEIN TTL-Delete mehr fuer `queue` — Queue-Rows sind per
        // Definition ungesyncte Arbeit (wartende Requests) und verfallen
        // nicht stumm, nur weil sie >48h alt sind (vorher ging ein laenger
        // offline arbeitender Einsatz beim naechsten Online-Kontakt verloren).
        // Sichtbarkeit/expliziter Verwurf kommt mit der Sync-Status-UI
        // (#1351-M8).
        // Drafts with updatedAt < cutoff
        await db.drafts.where("updatedAt").below(cutoff).delete();
        // F-04 (#1110): Klientel-/Case-/Event-Bundles tragen `expiresAt` im
        // verschluesselten Envelope des `clients`-Records (kein Index). Jeden
        // abgelaufenen Bundle-Spiegel (clients/cases/clean-events) verwerfen —
        // sonst ueberlebt veraltetes PII die 48h-TTL, solange der Key lebt.
        // Unsynced Events dieses Klienten ueberleben (Refs #1353, siehe
        // removeOfflineClient).
        await purgeExpiredBundles(ts);
    }

    /*
     * F-04 (#1110): Entferne jeden Klientel-Bundle, dessen `expiresAt` in der
     * Vergangenheit liegt. `expiresAt` steckt im verschluesselten
     * clients-Envelope, daher muss jeder client-Record entschluesselt werden.
     * Ein Decrypt-Fehler (Key rotiert/Salt gewechselt) ist selbst ein
     * Invalidierungsgrund -> Auto-Discard. Bundles ohne `expiresAt`
     * (Altdaten vor diesem Feld) bleiben unangetastet.
     */
    async function purgeExpiredBundles(now) {
        const crypto = _crypto();
        const ts = now || Date.now();
        const rows = await db.clients.toArray();
        for (const row of rows) {
            let envelope;
            try {
                envelope = await crypto.decryptPayload(row.data);
            } catch (e) {
                if (_isTransientDecryptError(e)) {
                    // Refs #1352: kein Schluessel geladen — ob der Bundle
                    // abgelaufen ist, laesst sich gerade nicht entscheiden;
                    // Row unangetastet lassen statt zu verwerfen.
                    continue;
                }
                // Permanent unentschlüsselbar -> der Schlüssel passt nicht
                // mehr (Salt/Passwort gewechselt) -> Security-Klasse wie der
                // Rechteentzug-Purge (F-10): der Bestand ist ohnehin
                // unlesbar und soll vollstaendig weg, auch unsynced Events
                // (#576/F-03, Refs #1353).
                await removeOfflineClient(row.pk, { force: true });
                continue;
            }
            if (_isExpired(envelope.expiresAt, ts)) {
                // Refs #1353: TTL-Ablauf ist KEIN Loeschgrund fuer unsynced
                // Arbeit — ohne force bleiben modified/new/conflict/dead
                // Events erhalten, nur der Server-Spiegel (clients/cases/
                // clean-events) faellt weg.
                await removeOfflineClient(row.pk);
            }
        }
    }

    /*
     * True, wenn `expiresAt` (ISO-8601-String vom Server) echt vor `now`
     * (ms-Epoch) liegt. Fehlt/ungueltig der Wert, gilt der Bundle NICHT als
     * abgelaufen (fail-open gegen versehentliches Loeschen frischer Daten;
     * der Server-Re-Fetch und der TTL-Index der Queue fangen das ab).
     */
    function _isExpired(expiresAt, now) {
        if (!expiresAt) return false;
        const exp = Date.parse(expiresAt);
        if (Number.isNaN(exp)) return false;
        return exp < (now || Date.now());
    }

    /*
     * F-05 (#1425, ADR-022): True wenn ein Bundle-Envelope eine andere
     * `schemaVersion` traegt als der aktuelle `BUNDLE_SCHEMA_VERSION` --
     * inklusive fehlendem/`undefined` Wert. Bundles ohne `schemaVersion` gab
     * es nie (das Feld ist seit dem ersten Bundle-Commit gesetzt), trotzdem
     * fail-closed: ein fehlender Wert gilt als Mismatch statt als
     * automatisch gueltig.
     */
    function _isSchemaMismatch(schemaVersion) {
        return schemaVersion !== BUNDLE_SCHEMA_VERSION;
    }

    async function count(table) {
        return db[table].count();
    }

    /*
     * Refs #1414: True, wenn `e` ein QuotaExceededError ist (Chromium:
     * `DOMException` mit `name === 'QuotaExceededError'`). Dexie reicht den
     * Fehler beim Transaktions-Abbruch weiter; je nach Pfad steht der Name
     * direkt auf `e` oder im gekapselten `e.inner`.
     */
    function _isQuotaError(e) {
        if (!e) return false;
        if (e.name === "QuotaExceededError") return true;
        return Boolean(e.inner && e.inner.name === "QuotaExceededError");
    }

    /*
     * Save a full offline client bundle produced by the server:
     *   {client, cases, workitems, events, document_types, generated_at, ttl}
     *
     * Each subtree lands in its own Dexie table with the payload encrypted
     * under the session key. The `clients` row also stores the list of
     * document types and workitems in its `data` envelope, so reading a
     * client is a single decrypt even for rarely-visited records.
     */
    async function saveClientBundle(bundle, etag = null) {
        const crypto = _crypto();
        const pk = bundle.client && bundle.client.pk;
        if (!pk) throw new Error("MalformedBundle");
        const now = Date.now();

        // Refs #1414: ALLE WebCrypto-`encrypt()`-Aufrufe VOR `db.transaction`.
        // Ein `await` auf ein Nicht-IDB-Promise (WebCrypto) innerhalb einer
        // Dexie-`rw`-Transaktion schliesst diese vorzeitig (stiller
        // Teil-Commit). Darum hier vorab ALLE Envelopes verschluesseln; die
        // Transaktion unten fuehrt ausschliesslich IDB-Operationen aus.
        const clientEnvelope = await crypto.encryptPayload({
            client: bundle.client,
            document_types: bundle.document_types || [],
            workitems: bundle.workitems || [],
            // Refs #1398 (P3): zuweisbare Nutzer:innen (Staff-Roster, nur
            // fuer Staff+ befuellt) fuers Offline-Create-Dropdown
            // ``assigned_to`` mitspeichern — sonst fehlt der Overlay-
            // Render-Pfad die Anzeigenamen und der Create-Replay koennte
            // niemanden zuweisen.
            assignable_users: bundle.assignable_users || [],
            generatedAt: bundle.generated_at,
            expiresAt: bundle.expires_at,
            ttl: bundle.ttl,
            schemaVersion: bundle.schema_version,
        });

        const caseRows = [];
        for (const caseRec of bundle.cases || []) {
            caseRows.push({
                pk: caseRec.pk,
                clientPk: pk,
                lastSynced: now,
                data: await crypto.encryptPayload(caseRec),
            });
        }

        const eventRows = [];
        for (const event of bundle.events || []) {
            eventRows.push({
                pk: event.pk,
                clientPk: pk,
                occurredAt: event.occurred_at,
                localStatus: "clean",
                data: await crypto.encryptPayload(event),
            });
        }

        // Refs #1414: Remove-Altbestand + Survivor-Scan + alle Puts ATOMAR in
        // EINER `rw`-Transaktion. Bricht ein Write ab (Quota, Tab-Kill), rollt
        // Dexie die gesamte Transaktion zurueck — der alte Bundle-Stand bleibt
        // vollstaendig erhalten (kein Partial-Bundle, das `getOfflineClient`
        // faelschlich als „vollstaendig" rendert). Innerhalb der Transaktion
        // ausschliesslich IDB-Operationen (kein WebCrypto-`await`, s.o.).
        let survivingEdits = 0;
        try {
            await db.transaction("rw", db.clients, db.cases, db.events, async () => {
                // Remove stale per-client state in case of a re-sync (Refs
                // #1353: Re-Take ist KEIN Sicherheits-Purge — ohne force
                // ueberleben unsynced Events dieses Klienten). Joint via
                // Dexie-Zone dieselbe Transaktion (nur IDB-Operationen).
                await removeOfflineClient(pk);

                // Refs #1353 (Ueberschreib-Falle): Nach dem non-force-Remove
                // koennen noch unsynced events-Rows dieses Klienten
                // existieren. Die Bundle-Schleife unten schreibt jedes Event
                // mit localStatus:"clean" — traefe sie eine ueberlebende
                // unsynced Row derselben pk, wuerde der lokale Edit durch die
                // Server-Version ueberschrieben und waere verloren. Deshalb
                // zuerst die pks der Ueberlebenden sammeln; die Bundle-Schleife
                // ueberspringt sie. Der Edit-Envelope traegt via
                // `expectedUpdatedAt` den Konflikt-Token — Replay/409 klaert
                // die Divergenz, nicht der Re-Take.
                const survivingUnsyncedPks = new Set(
                    (
                        await db.events
                            .where("clientPk")
                            .equals(pk)
                            .filter((e) => e.localStatus && e.localStatus !== "clean")
                            .toArray()
                    ).map((e) => e.pk)
                );
                survivingEdits = survivingUnsyncedPks.size;

                // Refs #1410 (a): den vom Server gelieferten Content-ETag
                // PLAINTEXT in der clients-Row ablegen (nicht in den
                // verschluesselten Envelope — er muss vor dem Decrypt als
                // ``If-None-Match`` sendbar sein; ein ETag ist wie ``pk``/
                // ``lastSynced`` keine PII, sondern ein Content-Hash). Nicht-
                // indiziertes Feld ⇒ kein Dexie-Version-Bump noetig. Fehlt der
                // ETag (Header nicht gesetzt), bleibt er ``null`` ⇒ die naechste
                // Revalidierung schickt kein If-None-Match und holt voll (200).
                await db.clients.put({ pk: pk, lastSynced: now, data: clientEnvelope, etag: etag || null });
                for (const row of caseRows) {
                    await db.cases.put(row);
                }
                for (const row of eventRows) {
                    if (survivingUnsyncedPks.has(row.pk)) continue;
                    await db.events.put(row);
                }
            });
        } catch (e) {
            // Refs #1414: QuotaExceededError gesondert und SICHTBAR melden
            // (kein stilles Ueberspringen). Die Transaktion ist bereits
            // zurueckgerollt — der alte Bundle-Stand bleibt intakt. Als
            // stabilen Vertrag fuer die aufrufende Fehler-UI (M17-anschluss-
            // faehig) auf einen Error mit `name === "QuotaExceededError"`
            // normalisieren.
            if (_isQuotaError(e)) {
                const quotaErr = new Error("OfflineQuotaExceeded");
                quotaErr.name = "QuotaExceededError";
                throw quotaErr;
            }
            throw e;
        }

        // Refs #1351/#1385 (M8/Task 4): Re-Take-Rueckmeldung — wie viele
        // ungesyncte Aenderungen dieses Klienten den (Re-)Sync ueberlebt
        // haben (Anzeige "<N> lokale Aenderungen beibehalten" im Aufrufer).
        return { survivingEdits: survivingEdits };
    }

    /*
     * Refs #1111: Bring a decrypted `events`-row into one canonical shape the
     * offline viewer can render, regardless of whether it is a CLEAN bundle
     * event or an offline EDIT envelope written by `offline-edit.js`.
     *
     * A clean record's `data` is the full serialized event (carries its own
     * `pk`/`occurred_at`/`document_type_name`/`data_fields`). A modified or
     * conflict record's `data` is the edit envelope (`formData`,
     * `expectedUpdatedAt`, `documentTypeName`, `documentTypePk`, `occurredAt`)
     * — markEventModified overwrites the clean record, so we reconstruct the
     * display/edit fields from the envelope here instead of leaving the row
     * with undefined date/type/values.
     */
    function normalizeOfflineEventRecord(r) {
        const data = r.data || {};
        const localStatus = r.localStatus || "clean";
        if (data.pk) {
            // Clean serialized event — pass through, only attach status.
            return { ...data, localStatus: localStatus };
        }
        // Edit envelope (modified/conflict) — map back to the canonical event
        // keys the read template + edit form expect.
        return {
            pk: r.pk,
            occurred_at: data.occurredAt || r.occurredAt || "",
            document_type_name: data.documentTypeName || "",
            document_type_pk: data.documentTypePk || "",
            // Refs #1111: Datei-Marker (display-only) wieder einmischen, damit der
            // "Datei vorhanden"-Hinweis nicht verschwindet, solange der Edit pending
            // ist. formData enthaelt keine FILE-Felder (nicht offline-editierbar).
            data_fields: Object.assign({}, data.formData || {}, data.fileMarkers || {}),
            updated_at: data.expectedUpdatedAt || "",
            localStatus: localStatus,
            // Refs #1351/#1385 (M8/Task 4): deadReason aus dem Envelope
            // durchreichen — der Offline-Viewer braucht ihn fuers dead-Badge
            // (deadReason-Mapping ueber data-*-Texte, siehe
            // offline-client-view.js).
            deadReason: data.deadReason || "",
        };
    }

    /*
     * Refs #1398 (P3): Record-Diskriminator fuer den WorkItem-Track (Spiegel
     * von ``_isWorkItemRecord`` in offline-edit.js — der `kind`-Marker liegt IM
     * verschluesselten ``data``, nie top-level). WorkItem-Records teilen sich
     * die ``events``-Tabelle mit Events; ``getOfflineClient`` muss sie darum
     * beim Rendern trennen: nur echte Events gehen durch
     * ``normalizeOfflineEventRecord``, WorkItems bilden ein eigenes Overlay
     * ueber die Bundle-``workitems``.
     */
    function _isWorkItemRow(r) {
        return Boolean(r && r.data && r.data.kind === "workitem");
    }

    /*
     * Refs #1398 (P3): Einen dekryptierten WorkItem-``events``-Record in die
     * kanonische WorkItem-Anzeige-Form bringen (Spiegel von
     * ``normalizeOfflineEventRecord`` fuer den WorkItem-Track).
     *
     * Der Record traegt IMMER ein Edit-Envelope (``data.formData`` mit den
     * WorkItemForm-Feldnamen + ``expectedUpdatedAt``) — anders als Events gibt
     * es keinen CLEAN-Bundle-Record in dieser Tabelle (WorkItems liegen im
     * clients-Envelope). Wir mappen die Formularfelder zurueck auf die
     * Bundle-Feldnamen (``assigned_to`` → ``assigned_to_pk``), damit
     * Overlay-Merge und Edit-Prefill dieselbe Form sehen wie ein Bundle-WorkItem.
     */
    function normalizeOfflineWorkItemRecord(r) {
        const data = r.data || {};
        const fd = data.formData || {};
        return {
            pk: r.pk,
            title: fd.title || "",
            description: fd.description || "",
            priority: fd.priority || "normal",
            item_type: fd.item_type || "task",
            due_date: fd.due_date || "",
            remind_at: fd.remind_at || "",
            recurrence: fd.recurrence || "none",
            assigned_to_pk: fd.assigned_to || "",
            updated_at: data.expectedUpdatedAt || "",
            localStatus: r.localStatus || "clean",
            deadReason: data.deadReason || "",
        };
    }

    /*
     * Refs #1398 (P3): Die Bundle-``workitems`` mit den offline erfassten
     * WorkItem-Records ueberlagern — analog zum Event-Overlay:
     *   - modified/conflict/dead ueber einen bestehenden Bundle-WorkItem:
     *     editierbare Felder aus dem Envelope ziehen, nicht editierbare
     *     Bundle-Felder (``status``/``can_edit``) bewahren, ``localStatus`` setzen;
     *   - offline NEU angelegte WorkItems (kein Bundle-Pendant): als neue Zeile
     *     mit ``localStatus:"new"`` VORNE einreihen (erscheinen sofort).
     */
    function _mergeWorkItemOverlay(bundleWorkitems, workitemRows) {
        const overlay = new Map();
        for (const row of workitemRows) {
            overlay.set(row.pk, normalizeOfflineWorkItemRecord(row));
        }
        const merged = (bundleWorkitems || []).map((wi) => {
            const ov = overlay.get(wi.pk);
            if (!ov) {
                return Object.assign({}, wi, { localStatus: "clean" });
            }
            overlay.delete(wi.pk);
            return Object.assign({}, wi, {
                title: ov.title,
                description: ov.description,
                priority: ov.priority,
                item_type: ov.item_type,
                due_date: ov.due_date,
                remind_at: ov.remind_at,
                recurrence: ov.recurrence,
                assigned_to_pk: ov.assigned_to_pk,
                updated_at: ov.updated_at || wi.updated_at,
                localStatus: ov.localStatus,
                deadReason: ov.deadReason,
            });
        });
        // Uebrig gebliebene Overlays = offline neu angelegte WorkItems ohne
        // Bundle-Pendant. Ersteller:in darf sie editieren (``can_edit:true``),
        // Status defaultet auf "open".
        const created = Array.from(overlay.values()).map((ov) =>
            Object.assign({}, ov, { status: ov.status || "open", can_edit: true })
        );
        return created.concat(merged);
    }

    async function getOfflineClient(pk) {
        const crypto = _crypto();
        const row = await db.clients.get(pk);
        if (!row) return null;
        let envelope;
        try {
            envelope = await crypto.decryptPayload(row.data);
        } catch (e) {
            if (_isTransientDecryptError(e)) {
                // Refs #1352: kein Schluessel geladen — Row behalten, nur
                // null liefern (Idle-Lock #1324 statt Loeschentscheidung).
                return null;
            }
            // Permanent unentschluesselbar -> restloser F-03-Purge wie in
            // purgeExpiredBundles, sonst blieben cases dieses Klienten als
            // verwaiste Chiffrate liegen (die pk wird ohne clients-Row nie
            // wieder besucht) (#576/F-03, Refs #1352).
            await removeOfflineClient(pk, { force: true });
            return null;
        }

        // F-05 (#1425, ADR-022): ein Bundle mit veralteter/fehlender
        // `schemaVersion` (nicht-abwaertskompatibler Layout-Wechsel) darf
        // ebenfalls nicht gerendert werden. Gleiches Muster wie das
        // TTL-Gate direkt darunter — OHNE force (Refs #1353): nur der
        // Server-Spiegel faellt weg, unsynced Events dieses Klienten
        // bleiben erhalten. "Re-Fetch anstoßen" (Akzeptanzkriterium) ist
        // hier bewusst kein neuer Mechanismus: der naechste Online-Kontakt
        // (revalidateCachedClients) bzw. ein erneutes "Offline mitnehmen"
        // schreibt ohnehin ein frisches Bundle mit aktueller Version.
        if (_isSchemaMismatch(envelope.schemaVersion)) {
            await removeOfflineClient(pk);
            return null;
        }

        // F-04/F-10 (#1110): Ein abgelaufener Bundle darf NICHT mehr gerendert
        // werden (veraltetes/anonymisiertes PII). Beim Lesen verwerfen — OHNE
        // force (Refs #1353): der Server-Spiegel (clients/cases/clean-events)
        // faellt weg, unsynced Events dieses Klienten bleiben erhalten und
        // sind hier bewusst NICHT Teil von "so tun, als sei nichts gecacht".
        if (_isExpired(envelope.expiresAt)) {
            await removeOfflineClient(pk);
            return null;
        }

        const cases = await listDecrypted("cases", (r) => r.clientPk === pk);
        const allRows = await listDecrypted("events", (r) => r.clientPk === pk);
        // Refs #1398 (P3): WorkItem-Records aus der Event-Liste heraustrennen —
        // sonst rendert normalizeOfflineEventRecord sie als (kaputte) Events.
        const eventRows = allRows.filter((r) => !_isWorkItemRow(r));
        const workitemRows = allRows.filter((r) => _isWorkItemRow(r));

        return {
            pk: pk,
            lastSynced: row.lastSynced,
            client: envelope.client,
            documentTypes: envelope.document_types,
            // Refs #1398 (P3): Bundle-WorkItems mit dem Offline-Overlay (neu +
            // modifiziert/conflict/dead) zusammenfuehren, inkl. localStatus fuer
            // die Status-Badges.
            workitems: _mergeWorkItemOverlay(envelope.workitems, workitemRows),
            // Refs #1398 (P3): zuweisbare Nutzer:innen fuers Create-Dropdown.
            assignableUsers: envelope.assignable_users || [],
            generatedAt: envelope.generatedAt,
            expiresAt: envelope.expiresAt,
            ttl: envelope.ttl,
            schemaVersion: envelope.schemaVersion,
            cases: cases.map((r) => r.data),
            // Surface the indexed `localStatus` alongside the decrypted
            // payload so the offline-detail template can badge unsynced
            // or conflicting edits without another IndexedDB round-trip.
            events: eventRows
                .map((r) => normalizeOfflineEventRecord(r))
                .sort((a, b) => (a.occurred_at < b.occurred_at ? 1 : -1)),
        };
    }

    async function listOfflineClients() {
        // Return a thin list (pk + lastSynced) without decrypting every
        // bundle — the detail view decrypts on demand.
        const rows = await db.clients.toArray();
        return rows.map((r) => ({ pk: r.pk, lastSynced: r.lastSynced }));
    }

    /*
     * Refs #1321: Anzeige-Liste fuer den Offline-Arbeitsplatz. Pseudonym und
     * `expiresAt` stecken im verschluesselten clients-Envelope, daher wird
     * dieser pro Zeile EINMAL entschluesselt (die schwereren cases/events
     * bleiben ungelesen). Abgelaufene Bundles (TTL) werden uebersprungen; ein
     * Decrypt-Fehler (kein/falscher Schluessel nach Idle-Wipe/Re-Login) laesst
     * die Zeile still aus — die Home zeigt dann ihren "bitte neu anmelden"-
     * Zustand. Bewusst read-only: kein Purge als Render-Seiteneffekt (das
     * uebernehmen getOfflineClient beim Lesen und der online-Re-Validate).
     */
    async function listOfflineClientsDetailed() {
        const crypto = _crypto();
        const rows = await db.clients.toArray();
        const out = [];
        for (const row of rows) {
            let envelope;
            try {
                envelope = await crypto.decryptPayload(row.data);
            } catch (_e) {
                continue;
            }
            // F-05 (#1425, ADR-022): List-Gate analog zum getOfflineClient-Gate
            // oben -- bewusst read-only (kein Purge als Render-Seiteneffekt,
            // wie schon beim TTL-Ausschluss darunter): ein Bundle mit
            // veralteter/fehlender schemaVersion wird nur nicht gelistet, der
            // tatsaechliche Purge passiert beim naechsten getOfflineClient-Read
            // bzw. der naechsten Online-Revalidierung.
            if (_isSchemaMismatch(envelope.schemaVersion)) continue;
            if (_isExpired(envelope.expiresAt)) continue;
            const client = envelope.client || {};
            out.push({
                pk: row.pk,
                pseudonym: client.pseudonym || "",
                // SI-2 (#1520/#1499): Kontaktstufe fuer den Person-Picker-
                // Vorfilter der Offline-Create-Shell durchreichen (Server
                // liefert `contact_stage` bereits im Klient-Bundle) — DocTypes
                // mit `min_contact_stage` sind bei "ohne Person"/anonym nicht
                // waehlbar (SI-4).
                contactStage: client.contact_stage || "",
                lastSynced: row.lastSynced,
                expiresAt: envelope.expiresAt || "",
            });
        }
        out.sort((a, b) => (a.pseudonym || "").localeCompare(b.pseudonym || "", "de"));
        return out;
    }

    /*
     * Refs #1353: Invariante — Rows mit `localStatus` in
     * {modified, new, conflict, dead} ("unsynced") loescht NUR: (1) eine
     * explizite Nutzeraktion (P1/M8), (2) der Security-Purge bei
     * Rechteentzug/Loeschung (`revalidateCachedClient` bei 404/410,
     * F-10/#1110), (3) `purgeAll` (Logout), (4) der permanente
     * Decrypt-Fehler-Discard aus M1 (#1352). TTL-Ablauf und Re-Take gehoeren
     * NICHT dazu.
     *
     * Ohne `{force: true}` (Default) loescht diese Funktion daher nur die
     * `clients`-Row, alle `cases` des Klienten und NUR jene `events` mit
     * `localStatus === "clean"` (bzw. fehlendem `localStatus` bei
     * Altdaten) — unsynced Events bleiben erhalten. Mit `{force: true}`
     * gilt weiterhin das alte Verhalten: restlos alles weg, fuer die vier
     * oben genannten erlaubten Loeschgruende.
     */
    async function removeOfflineClient(pk, opts) {
        const force = !!(opts && opts.force);
        await db.clients.delete(pk);
        await db.cases.where("clientPk").equals(pk).delete();
        if (force) {
            await db.events.where("clientPk").equals(pk).delete();
        } else {
            await db.events
                .where("clientPk")
                .equals(pk)
                .filter((e) => !e.localStatus || e.localStatus === "clean")
                .delete();
        }
    }

    async function isClientOffline(pk) {
        return (await db.clients.where("pk").equals(pk).count()) > 0;
    }

    async function countOfflineClients() {
        return db.clients.count();
    }

    /*
     * F-10 (#1110): Beim naechsten Online-Kontakt jeden gecachten Klientel
     * gegen den Server re-validieren. Der Bundle-Endpoint wendet alle
     * Sichtbarkeits-Gates serverseitig an und liefert 404, sobald der Klient
     * geloescht ist oder nicht mehr im Facility-Scope/der Rolle des Users
     * liegt (`get_object_or_404(... facility=...)` in views/offline.py).
     *
     * Invalidierungs-Statuscodes (401/404/410) -> lokalen Eintrag samt
     * cases/events purgen (Loeschung/Anonymisierung, F-10/#1110 — der
     * force-Purge aus M2/#1353 bleibt unveraendert): der Klient darf offline
     * nicht im Klartext fortbestehen. 200 -> frisches (ggf. anonymisiertes/
     * leeres) Bundle zurueckschreiben, damit eine serverseitige
     * Anonymisierung den lokalen Klartext ueberschreibt. Netz-/Serverfehler
     * (offline, 5xx, sonstige) lassen den Cache UNANGETASTET — fail-open,
     * sonst wuerde ein flapatender Server den Aussendienst-Cache leeren.
     *
     * Refs #1354: 403 ist bewusst KEIN Invalidierungs-Status mehr. Echter
     * Rechteentzug erreicht den Client nie als 403 — `signals/
     * offline_invalidation.py` rotiert bei Entzug den Salt UND flusht die
     * Sessions serverseitig, der naechste Bundle-Fetch landet also auf dem
     * Login-Redirect (dem `fetch` folgt) -> eine 200-Loginseite, deren
     * `response.json()` wirft -> "error", Cache bleibt. Die eigentliche
     * Datenvernichtung nach Entzug leistet ohnehin die Salt-Rotation ueber
     * den permanenten Decrypt-Fehler (M1/#1352). Ein tatsaechliches 403 ist
     * damit CSRF-/Rate-Limit-/Proxy-Rauschen -> Cache behalten statt purgen.
     *
     * Idempotent und ohne Seiteneffekt auf laufende Edits: ein Klient mit
     * lokal modifizierten/konfligierenden Events wird NICHT re-fetcht
     * (sonst wuerde der Re-Fetch unsynced Klartext ueberschreiben). Diese
     * werden ueber den Queue-/Edit-Replay separat behandelt.
     */
    const INVALIDATION_STATUSES = [401, 404, 410];

    function _bundleUrl(clientPk) {
        return "/api/v1/offline/bundle/client/" + encodeURIComponent(clientPk) + "/";
    }

    /*
     * Liefert die ANZAHL ungesyncter Events (modified/new/conflict/dead)
     * dieses Klienten — nicht nur bool. `revalidateCachedClient` unten
     * braucht nur Truthy/Falsy (eine Number > 0 ist truthy, unveraendertes
     * Verhalten); Refs #1351/#1385 (M8/Task 4) braucht den echten Zaehlwert
     * fuer die Toggle-Warnung (`countUnsyncedEventsFor`) und den
     * bool-Wrapper (`hasUnsyncedEventsFor`).
     */
    async function _hasUnsyncedEvents(pk) {
        return db.events
            .where("clientPk")
            .equals(pk)
            .filter((e) => e.localStatus && e.localStatus !== "clean")
            .count();
    }

    /*
     * Refs #1351/#1385 (M8/Task 4): oeffentlicher, benannter Wrapper um das
     * bisher rein interne `_hasUnsyncedEvents`-Praedikat (nur von
     * `revalidateCachedClient` genutzt) — fuer die Toggle-Warnung vor
     * `removeClientFromOffline` exportiert, damit Konsumenten nicht auf eine
     * `_`-praefigierte Funktion zugreifen muessen.
     */
    async function hasUnsyncedEventsFor(pk) {
        return (await _hasUnsyncedEvents(pk)) > 0;
    }

    /*
     * Refs #1351/#1385: echter Zaehlwert (statt nur bool) fuer die
     * Toggle-Warnung — der Confirm-Text nennt die Anzahl ungesyncter
     * Aenderungen, die beim Entfernen aus dem Offline-Cache lokal erhalten
     * bleiben (S1: kein Datenverlust, nur ein Hinweis).
     */
    async function countUnsyncedEventsFor(pk) {
        return _hasUnsyncedEvents(pk);
    }

    async function revalidateCachedClient(pk) {
        // Refs #1410 (a): gespeicherten Content-ETag lesen und als
        // ``If-None-Match`` mitschicken. Trifft er serverseitig, antwortet der
        // Endpoint 304 (kein Body) und wir sparen die volle Bundle-Uebertragung.
        // Fehlt der ETag (Altbestand vor #1410, oder Server lieferte keinen),
        // bleibt es beim vollen 200-Fetch — kein Bruch.
        const existing = await db.clients.get(pk);
        const storedEtag = existing && existing.etag;
        const headers = { Accept: "application/json" };
        if (storedEtag) headers["If-None-Match"] = storedEtag;

        let response;
        try {
            response = await fetch(_bundleUrl(pk), {
                method: "GET",
                credentials: "same-origin",
                headers: headers,
            });
        } catch (_e) {
            // Offline / Netzfehler -> Cache bewusst behalten.
            return "error";
        }

        // Refs #1354: 429 (Rate-Limit) ist weder Rechteentzug noch eine
        // Aussage ueber den Klienten selbst -> eigenes Ergebnis, Cache
        // unangetastet. `revalidateCachedClients` bricht die Batch-Schleife
        // darauf ab, statt das Request-Budget weiter zu verbrennen.
        if (response.status === 429) {
            return "ratelimited";
        }

        // F-10 (#1110/#1111): Zugriff entzogen oder Client weg -> IMMER purgen,
        // AUCH bei offenen unsynced Edits. Entschluesselte PII darf nach
        // Rechteentzug nicht offline ueberleben; ein haengender Edit darf den
        // Sicherheits-Purge nicht blockieren. (Vorher stand der unsynced-Check
        // davor und uebersprang den Purge -> Befund #1111.) Refs #1353: dies
        // ist einer der wenigen erlaubten Loeschgruende fuer unsynced Arbeit
        // -> bewusst mit {force: true}, unveraendert gegenueber F-10.
        if (INVALIDATION_STATUSES.includes(response.status)) {
            await removeOfflineClient(pk, { force: true });
            return "purged";
        }

        // Refs #1410 (a): 304 = der Server bestaetigt, dass sich das Bundle seit
        // dem gespeicherten ETag nicht geaendert hat. Kein Body, kein Re-Save,
        // Cache bleibt exakt wie er ist. Bewusst VOR dem unsynced-Check und dem
        // ok-Zweig: ein 304 kann per Definition nichts ueberschreiben.
        if (response.status === 304) {
            return "not-modified";
        }

        // Zugriff weiterhin gueltig: lokale unsynced Aenderungen NICHT mit
        // Server-Daten ueberschreiben -> Refresh ueberspringen.
        if (await _hasUnsyncedEvents(pk)) return "skipped";

        if (response.ok) {
            try {
                const bundle = await response.json();
                // Refs #1410 (a): neuen ETag mitspeichern, damit die naechste
                // Revalidierung wieder bedingt fragen kann.
                const newEtag = response.headers.get("ETag");
                await saveClientBundle(bundle, newEtag);
                return "refreshed";
            } catch (_e) {
                return "error";
            }
        }
        // 5xx oder unerwartet -> nichts tun.
        return "error";
    }

    async function revalidateCachedClients() {
        const rows = await db.clients.toArray();
        let purged = 0;
        let refreshed = 0;
        let ratelimited = false;
        for (const row of rows) {
            const result = await revalidateCachedClient(row.pk);
            if (result === "purged") purged += 1;
            else if (result === "refreshed") refreshed += 1;
            else if (result === "ratelimited") {
                // Refs #1354: Budget nicht weiter verbrennen -> Schleife
                // abbrechen; das naechste online-Event/Boot revalidiert die
                // restlichen Klienten erneut.
                ratelimited = true;
                break;
            }
        }
        return { purged: purged, refreshed: refreshed, total: rows.length, ratelimited: ratelimited };
    }

    /* ─── SI-2 (#1520/#1499) — personenloses Facility-Meta-Bundle ─────────── */

    function _facilityBundleUrl() {
        return "/api/v1/offline/bundle/facility/";
    }

    /*
     * SI-2 (#1520/#1499): Spiegel von `_isSchemaMismatch` fuer die EIGENE
     * Facility-Schema-Version. Fail-closed: ein fehlender/`undefined` Wert
     * gilt als Mismatch (kein automatisches Gueltig).
     */
    function _isFacilitySchemaMismatch(schemaVersion) {
        return schemaVersion !== FACILITY_BUNDLE_SCHEMA_VERSION;
    }

    /*
     * SI-2 (#1520/#1499): Personenloses Facility-Meta-Bundle vom Server
     * speichern (Analog `saveClientBundle`, aber ein Singleton ohne
     * Roster/PII):
     *   {schema_version, generated_at, ttl, expires_at, document_types,
     *    assignable_users}
     *
     * Das GESAMTE Envelope wird VOR `db.transaction` verschluesselt (#1414):
     * ein WebCrypto-`await` innerhalb einer Dexie-`rw`-Transaktion schliesst
     * diese vorzeitig (stiller Teil-Commit). Die Transaktion unten fuehrt
     * ausschliesslich IDB-Operationen aus. Der Content-ETag liegt PLAINTEXT
     * auf der Row (wie bei `clients`) — er muss vor dem Decrypt als
     * `If-None-Match` sendbar sein und ist ein Content-Hash, keine PII.
     */
    async function saveFacilityBundle(bundle, etag = null) {
        const crypto = _crypto();
        if (!bundle || !bundle.schema_version) throw new Error("MalformedFacilityBundle");
        const now = Date.now();

        // Refs #1414: ALLE WebCrypto-`encrypt()` VOR `db.transaction`.
        const envelope = await crypto.encryptPayload({
            document_types: bundle.document_types || [],
            assignable_users: bundle.assignable_users || [],
            generatedAt: bundle.generated_at,
            expiresAt: bundle.expires_at,
            ttl: bundle.ttl,
            schemaVersion: bundle.schema_version,
        });

        try {
            await db.transaction("rw", db.facility, async () => {
                await db.facility.put({
                    key: FACILITY_ROW_KEY,
                    lastSynced: now,
                    etag: etag || null,
                    data: envelope,
                });
            });
        } catch (e) {
            // Refs #1414: QuotaExceededError sichtbar und stabil normalisiert
            // melden (kein stilles Ueberspringen) — die Transaktion ist bereits
            // zurueckgerollt, der alte Meta-Stand bleibt intakt.
            if (_isQuotaError(e)) {
                const quotaErr = new Error("OfflineQuotaExceeded");
                quotaErr.name = "QuotaExceededError";
                throw quotaErr;
            }
            throw e;
        }
    }

    /*
     * SI-2 (#1520/#1499): das gespeicherte Facility-Meta-Bundle lesen (Analog
     * `getOfflineClient`, ohne cases/events). Gleiche Gates: TRANSIENT-Decrypt
     * -> null ohne Loeschen (#1352); PERMANENT-Decrypt -> Row verwerfen
     * (personenlos, kein force-Sonderfall); Schema-Mismatch (gegen die eigene
     * FACILITY_BUNDLE_SCHEMA_VERSION) -> verwerfen; Expiry (48h) -> verwerfen.
     * Ein neuer Fetch (revalidateCachedFacility / Login-Bootstrap) schreibt
     * danach ein frisches Bundle.
     */
    async function getOfflineFacility() {
        const crypto = _crypto();
        const row = await db.facility.get(FACILITY_ROW_KEY);
        if (!row) return null;
        let envelope;
        try {
            envelope = await crypto.decryptPayload(row.data);
        } catch (e) {
            if (_isTransientDecryptError(e)) {
                // Refs #1352: kein Schluessel geladen — Row behalten, null liefern.
                return null;
            }
            // Permanent unentschluesselbar (Salt/Passwort rotiert) — verwerfen.
            await db.facility.delete(FACILITY_ROW_KEY);
            return null;
        }
        if (_isFacilitySchemaMismatch(envelope.schemaVersion)) {
            await db.facility.delete(FACILITY_ROW_KEY);
            return null;
        }
        if (_isExpired(envelope.expiresAt)) {
            await db.facility.delete(FACILITY_ROW_KEY);
            return null;
        }
        return {
            lastSynced: row.lastSynced,
            documentTypes: envelope.document_types || [],
            assignableUsers: envelope.assignable_users || [],
            generatedAt: envelope.generatedAt,
            expiresAt: envelope.expiresAt,
            ttl: envelope.ttl,
            schemaVersion: envelope.schemaVersion,
        };
    }

    /*
     * SI-2 (#1520/#1499): das Facility-Meta-Bundle gegen den Server
     * re-validieren (Analog `revalidateCachedClient`). Gespeicherten ETag als
     * `If-None-Match` mitschicken; 304 -> `not-modified` (Cache bleibt); 200 ->
     * frisch speichern; 429 -> `ratelimited`; INVALIDATION_STATUSES (401/404/
     * 410) -> lokalen Meta-Cache verwerfen (Rolle/Facility-Kontext verloren);
     * Netz-/Serverfehler -> `error`, Cache unangetastet (fail-open). Personenlos
     * -> kein unsynced-Edit-Skip und kein force-Sicherheits-Purge noetig; auch
     * fuer den Erst-Fetch nutzbar (keine Row -> kein ETag -> voller 200).
     */
    async function revalidateCachedFacility() {
        const existing = await db.facility.get(FACILITY_ROW_KEY);
        const storedEtag = existing && existing.etag;
        const headers = { Accept: "application/json" };
        if (storedEtag) headers["If-None-Match"] = storedEtag;

        let response;
        try {
            response = await fetch(_facilityBundleUrl(), {
                method: "GET",
                credentials: "same-origin",
                headers: headers,
            });
        } catch (_e) {
            // Offline / Netzfehler -> Cache bewusst behalten.
            return "error";
        }

        if (response.status === 429) {
            return "ratelimited";
        }
        if (INVALIDATION_STATUSES.includes(response.status)) {
            await db.facility.delete(FACILITY_ROW_KEY);
            return "purged";
        }
        if (response.status === 304) {
            return "not-modified";
        }
        if (response.ok) {
            try {
                const bundle = await response.json();
                const newEtag = response.headers.get("ETag");
                await saveFacilityBundle(bundle, newEtag);
                return "refreshed";
            } catch (_e) {
                return "error";
            }
        }
        // 5xx oder unerwartet -> nichts tun.
        return "error";
    }

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
        const { pk, clientPk, occurredAt, localStatus, data } = record;
        await db.events.put({
            pk: pk,
            clientPk: clientPk || "",
            occurredAt: occurredAt || "",
            localStatus: localStatus || "modified",
            data: await crypto.encryptPayload(data),
        });
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
            envelope = await crypto.decryptPayload(existing.data);
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
            data: await crypto.encryptPayload(envelope),
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
            envelope = await crypto.decryptPayload(existing.data);
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
            data: await crypto.encryptPayload(envelope),
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
            envelope = await crypto.decryptPayload(existing.data);
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
            data: await crypto.encryptPayload(envelope),
        });
        return nextStatus;
    }

    async function discardDeadEvent(pk) {
        // Nutzeraktion (S1-Ausnahme 1) — Pendant zu clearOfflineEdit fuer
        // den dead-Letter-Pfad.
        return clearOfflineEdit(pk);
    }

    /* ─── generische Queue: Listing + Nutzeraktionen (Refs #1351/#1384) ──── */

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
                    patterns && patterns.WORKITEM_STATUS && patterns.WORKITEM_STATUS.test(r.url || "")
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
            return { usage: usage, quota: quota, percent: Math.round((usage / quota) * 100) };
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

    window.offlineStore = {
        db: db,
        putEncrypted: putEncrypted,
        getDecrypted: getDecrypted,
        listDecrypted: listDecrypted,
        deleteRow: deleteRow,
        purgeAll: purgeAll,
        purgeExpired: purgeExpired,
        purgeExpiredBundles: purgeExpiredBundles,
        count: count,
        saveClientBundle: saveClientBundle,
        getOfflineClient: getOfflineClient,
        listOfflineClients: listOfflineClients,
        listOfflineClientsDetailed: listOfflineClientsDetailed,
        removeOfflineClient: removeOfflineClient,
        isClientOffline: isClientOffline,
        countOfflineClients: countOfflineClients,
        // F-10 (#1110) — Re-Validierung gegen den Server beim Online-Kontakt
        revalidateCachedClient: revalidateCachedClient,
        revalidateCachedClients: revalidateCachedClients,
        // SI-2 (#1520/#1499) — personenloses Facility-Meta-Bundle
        saveFacilityBundle: saveFacilityBundle,
        getOfflineFacility: getOfflineFacility,
        revalidateCachedFacility: revalidateCachedFacility,
        // Stage 3 (#575) — offline edit + conflict tracking
        saveOfflineEdit: saveOfflineEdit,
        getOfflineEvent: getOfflineEvent,
        listModifiedEvents: listModifiedEvents,
        listConflicts: listConflicts,
        countUnsyncedEvents: countUnsyncedEvents,
        countConflictEvents: countConflictEvents,
        // Refs #1351/#1385 — M8/Task 4: dead-Zaehler + Toggle-Warnung
        countDeadEvents: countDeadEvents,
        hasUnsyncedEventsFor: hasUnsyncedEventsFor,
        countUnsyncedEventsFor: countUnsyncedEventsFor,
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
        TTL_MS: TTL_MS,
        MAX_OFFLINE_CLIENTS: MAX_OFFLINE_CLIENTS,
        // F-05 (#1425) — Lesepfad-Gate vergleicht dagegen, siehe _isSchemaMismatch
        BUNDLE_SCHEMA_VERSION: BUNDLE_SCHEMA_VERSION,
        // SI-2 (#1520/#1499) — Facility-Meta-Bundle-Gate, siehe _isFacilitySchemaMismatch
        FACILITY_BUNDLE_SCHEMA_VERSION: FACILITY_BUNDLE_SCHEMA_VERSION,
    };

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
                if (cs && cs.ready) {
                    await cs.ready();
                }
                const hasKey = cs && cs.hasSessionKey ? cs.hasSessionKey() : false;
                // Refs #1352: ohne Schluessel keine Loeschentscheidung — das
                // Gate umschliesst jetzt auch purgeExpired(), nicht nur die
                // Re-Validierung. Ohne Key ist die Session ohnehin idle-
                // gelockt (#1324); der naechste Online-Kontakt mit
                // gueltigem Schluessel holt beides nach.
                if (hasKey) {
                    await purgeExpired(Date.now());
                    await revalidateCachedClients();
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
})();
