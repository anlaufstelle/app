/*
 * offline-bundles.js — Teil 2/4 des offline-store-Verbunds (Refs #1838):
 * Client-Bundles (Speichern/Lesen/Overlay-Merge/Normalisierer), die
 * Aggregations-Reader der Offline-Shells, Mitnahme-Verwaltung
 * (remove/isOffline/count) sowie die Client- und Facility-Revalidierung.
 * Architektur-Uebersicht + Dexie-Schema-Doku: offline-db.js. Laedt NACH
 * offline-db.js und VOR offline-revocations.js/offline-store.js
 * (Script-Tag-Reihenfolge fix, siehe base.html/offline_base.html).
 *
 * Refs #574, #618, #1110, #1499, #1520 — Historie siehe offline-db.js.
 */
(function () {
    "use strict";

    // Refs #1838: private Helfer aus offline-db.js — verbrauchbarer
    // Einmal-Kanal (Details + Sicherheitsbegruendung dort). Fail-closed wie
    // in offline-store.js: ohne Kanal bleibt das Modul inert.
    const _internals = window.__offlineStoreInternals && window.__offlineStoreInternals.take();
    if (!_internals) {
        // eslint-disable-next-line no-console
        console.error("[offline-bundles] interner Kanal fehlt — Ladereihenfolge pruefen (Refs #1838).");
        return;
    }
    const {
        db,
        _crypto,
        _aad,
        _isTransientDecryptError,
        _isExpired,
        _isSchemaMismatch,
        _isQuotaError,
        FACILITY_ROW_KEY,
        FACILITY_BUNDLE_SCHEMA_VERSION,
        listDecrypted,
        _UNSYNCED_EVENT_STATUS,
    } = _internals;

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
        }, _aad("clients", pk));

        const caseRows = [];
        for (const caseRec of bundle.cases || []) {
            caseRows.push({
                pk: caseRec.pk,
                clientPk: pk,
                lastSynced: now,
                data: await crypto.encryptPayload(caseRec, _aad("cases", caseRec.pk)),
            });
        }

        const eventRows = [];
        for (const event of bundle.events || []) {
            eventRows.push({
                pk: event.pk,
                clientPk: pk,
                // Refs #1476 (R9): occurred_at NICHT plaintext ablegen — steckt
                // im verschluesselten Envelope (`event.occurred_at`).
                localStatus: "clean",
                data: await crypto.encryptPayload(event, _aad("events", event.pk)),
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
            // Refs #1476 (R9): occurredAt kommt aus dem verschluesselten
            // Envelope, nicht mehr aus dem (entfernten) Plaintext-Row-Feld.
            occurred_at: data.occurredAt || "",
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
            envelope = await crypto.decryptPayload(row.data, _aad("clients", row.pk));
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
                envelope = await crypto.decryptPayload(row.data, _aad("clients", row.pk));
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
                // Refs #1520/#1499: Kontaktstufe fuer den Person-Picker-
                // Vorfilter der Offline-Create-Shell durchreichen (Server
                // liefert `contact_stage` bereits im Klient-Bundle) — DocTypes
                // mit `min_contact_stage` sind bei "ohne Person"/anonym nicht
                // waehlbar.
                contactStage: client.contact_stage || "",
                lastSynced: row.lastSynced,
                expiresAt: envelope.expiresAt || "",
                // Refs #1530/#1499: additive Felder fuer die Offline-
                // Personenliste (#1499) -- reine Passthroughs aus dem
                // Server-Bundle (offline.py `client`-Dict), keine
                // eigene Ableitung/Formatierung hier. Bestehende Konsumenten
                // (offline-home.js, offline-create.js) lesen weiterhin nur
                // die Felder oben; diese sind rein additiv.
                ageCluster: client.age_cluster || "",
                ageClusterDisplay: client.age_cluster_display || "",
                contactStageDisplay: client.contact_stage_display || "",
                // server-seitig cutoff-frei berechnet, ISO-8601-String
                // oder null (kontaktloser Klient) -- null bewusst NICHT auf
                // "" gemappt (JSON-clean Unterscheidung "kein Kontakt" vs.
                // "unbekannt" bleibt fuer die Personenliste erhalten).
                lastContact: client.last_contact != null ? client.last_contact : null,
                // Default true, falls ein Alt-Bundle das Feld (noch) nicht
                // gesetzt hat -- das Schema-Gate oben laesst nur v2-Bundles
                // durch, die is_active immer setzen; die Absicherung ist
                // defensiv, nicht der Regelfall.
                isActive: client.is_active !== false,
            });
        }
        out.sort((a, b) => (a.pseudonym || "").localeCompare(b.pseudonym || "", "de"));
        return out;
    }

    /*
     * W3 (#1499): Gemeinsame Basis fuer die aggregierten Reader
     * (`listOfflineEventsAggregated`/`listOfflineWorkItemsAggregated`).
     * Entschluesselt jede `clients`-Row EINMAL und liefert nur GUELTIGE (nicht
     * abgelaufene, schema-konforme) Bundles als Map `pk -> {pseudonym, envelope}`.
     * Spiegelt exakt das read-only List-Gate aus `listOfflineClientsDetailed`
     * (kein Purge als Render-Seiteneffekt) — der tatsaechliche Purge bleibt bei
     * `getOfflineClient`/Re-Validierung. Ein Decrypt-Fehler (Idle-Lock/Re-Login)
     * laesst die Zeile still aus.
     */
    async function _validClientBundles() {
        const crypto = _crypto();
        const rows = await db.clients.toArray();
        const map = new Map();
        for (const row of rows) {
            let envelope;
            try {
                envelope = await crypto.decryptPayload(row.data, _aad("clients", row.pk));
            } catch (_e) {
                continue;
            }
            if (_isSchemaMismatch(envelope.schemaVersion)) continue;
            if (_isExpired(envelope.expiresAt)) continue;
            map.set(row.pk, {
                pseudonym: (envelope.client || {}).pseudonym || "",
                envelope: envelope,
            });
        }
        return map;
    }

    // Refs #1353: die vier "unsynced"-Status, die Bundle-Ablauf ueberleben.
    // Refs #1838 (Konsolidierung, Integrator-Auflage): identische Menge wie
    // _UNSYNCED_EVENT_STATUS in offline-db.js — dort ist die eine Quelle,
    // geteilt ueber den internen Kanal (vorher zwei driftfaehige Kopien).
    const _UNSYNCED_STATUSES = _UNSYNCED_EVENT_STATUS;

    /*
     * W3-A (#1539/#1499): Chronologische "lokale Chronik" fuer die
     * Zeitstrom-Offline-Shell (`/`). Aggregiert Events ueber ALLE offline
     * mitgenommenen Personen hinweg PLUS personlose (anonyme) Offline-Eintraege
     * — jede `events`-Row EINMAL entschluesselt (via `listDecrypted`, das
     * Transient-/Permanent-Decrypt-Fehler bereits korrekt behandelt),
     * WorkItem-Rows heraus-gefiltert.
     *
     * Gate (spiegelt `getOfflineClient`/`listOfflineClientsDetailed`, read-only):
     *   - CLEAN Events (Server-Spiegel) erscheinen NUR, solange ihr Bundle
     *     gueltig ist (nicht abgelaufen, schema-konform) — sonst waere es
     *     veraltetes/anonymisiertes PII (F-04/F-10). Kein Purge hier; die Row
     *     wird nur ausgelassen.
     *   - UNSYNCED Events (modified/new/conflict/dead) erscheinen IMMER — sie
     *     ueberleben Bundle-Ablauf laut Kern-Invariante (S1) und sind die
     *     eigentliche lokale Arbeit. Personlose (`clientPk===""`) Eintraege sind
     *     stets unsynced-new und werden als anonym markiert (ohne Pseudonym).
     *
     * Sortierung: `occurred_at` absteigend (neueste zuerst), wie der Online-Feed.
     */
    async function listOfflineEventsAggregated() {
        const bundles = await _validClientBundles();
        const rows = await listDecrypted("events", (r) => !_isWorkItemRow(r));
        const out = [];
        for (const r of rows) {
            const clientPk = r.clientPk || "";
            const isUnsynced = _UNSYNCED_STATUSES.indexOf(r.localStatus) !== -1;
            // Stale CLEAN Event eines abgelaufenen/entfernten Bundles: auslassen.
            if (!isUnsynced && clientPk && !bundles.has(clientPk)) continue;
            const ev = normalizeOfflineEventRecord(r);
            ev.clientPk = clientPk;
            ev.isAnonymous = !clientPk;
            ev.pseudonym =
                clientPk && bundles.has(clientPk) ? bundles.get(clientPk).pseudonym : "";
            out.push(ev);
        }
        out.sort((a, b) => (a.occurred_at < b.occurred_at ? 1 : -1));
        return out;
    }

    /*
     * W3-B (#1540/#1499): Aggregierte "lokale Aufgaben"-Sicht fuer die
     * WorkItem-Offline-Shell (`/workitems/`). Fuehrt die Bundle-`workitems` JEDER
     * gueltigen offline mitgenommenen Person mit ihren Offline-Overlays
     * (neu/modified/conflict/dead) zusammen — Semantik `_mergeWorkItemOverlay`,
     * das bislang nur per-Client in `getOfflineClient` lief — und ergaenzt
     * personlose (standalone/anonyme) Offline-WorkItems.
     *
     * Gate wie bei den Events: nur gueltige Bundles liefern ihre `workitems`;
     * unsynced WorkItem-Rows eines abgelaufenen/entfernten Bundles ueberleben
     * (orphaned) und erscheinen ohne Pseudonym. Read-only (kein Purge).
     */
    async function listOfflineWorkItemsAggregated() {
        const bundles = await _validClientBundles();
        const wiRows = await listDecrypted("events", (r) => _isWorkItemRow(r));
        const byClient = new Map();
        const standalone = [];
        for (const r of wiRows) {
            const clientPk = r.clientPk || "";
            if (!clientPk) {
                standalone.push(r);
                continue;
            }
            if (!byClient.has(clientPk)) byClient.set(clientPk, []);
            byClient.get(clientPk).push(r);
        }
        const out = [];
        for (const [clientPk, meta] of bundles) {
            const merged = _mergeWorkItemOverlay(meta.envelope.workitems, byClient.get(clientPk) || []);
            for (const wi of merged) {
                out.push(
                    Object.assign({}, wi, {
                        clientPk: clientPk,
                        pseudonym: meta.pseudonym,
                        isAnonymous: false,
                    })
                );
            }
            byClient.delete(clientPk);
        }
        // Personlose (anonyme) Offline-WorkItems: nie ein Bundle-Pendant.
        for (const r of standalone) {
            const wi = normalizeOfflineWorkItemRecord(r);
            out.push(
                Object.assign({}, wi, {
                    status: wi.status || "open",
                    can_edit: true,
                    clientPk: "",
                    pseudonym: "",
                    isAnonymous: true,
                })
            );
        }
        // Orphaned: unsynced WorkItem-Rows, deren Bundle abgelaufen/entfernt ist,
        // ueberleben laut S1-Invariante und bleiben sichtbar (ohne Pseudonym).
        for (const [clientPk, rows] of byClient) {
            for (const r of rows) {
                const wi = normalizeOfflineWorkItemRecord(r);
                out.push(
                    Object.assign({}, wi, {
                        status: wi.status || "open",
                        can_edit: true,
                        clientPk: clientPk,
                        pseudonym: "",
                        isAnonymous: false,
                    })
                );
            }
        }
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

    /*
     * Refs #2067 (Cross-Review Finding 1): Archivierung der EINRICHTUNG.
     *
     * Der Absatz darueber erklaert, warum ein nacktes 403 bewusst KEIN
     * Invalidierungsgrund ist (CSRF-/Rate-Limit-/Proxy-Rauschen). Genau
     * deshalb setzt die Middleware auf ihrer Sperr-403 einen eigenen Header:
     * er unterscheidet "Rauschen" von "diese Einrichtung ist stillgelegt",
     * ohne 403 pauschal wieder zum Purge-Grund zu machen.
     *
     * Rechtsfolge ist haerter als bei 401/404/410: dort faellt EIN Klient weg,
     * hier der gesamte Zugang. Bundles allein zu loeschen genuegte nicht — der
     * abgeleitete Sitzungsschluessel liegt in einer zweiten IndexedDB und
     * entschluesselte, was ein spaeterer Fetch noch hereinholt. Gewipet wird
     * deshalb ueber denselben Pfad wie Logout und Idle-Timeout:
     * CryptoKey + Aktivitaetsstempel + alle Dexie-Tabellen.
     */
    const FACILITY_ARCHIVED_HEADER = "X-Facility-Archived";

    function _isFacilityArchived(response) {
        return response.status === 403 && response.headers.get(FACILITY_ARCHIVED_HEADER) === "1";
    }

    /*
     * Wipe-Pfad wie in sw-register.js (Logout): crypto.js besitzt den
     * gemeinsamen Ablauf, der Fallback greift nur, wenn crypto.js auf dieser
     * Seite gar nicht geladen ist. Fehler werden geschluckt — ein klemmender
     * Purge darf die Revalidierungs-Schleife nicht aufhalten; ohne Schluessel
     * ist das zurueckbleibende Chiffrat ohnehin unlesbar (F-01).
     */
    async function _wipeAllOfflineState() {
        try {
            if (window.crypto_session && window.crypto_session.wipeOfflineState) {
                await window.crypto_session.wipeOfflineState();
            } else if (window.offlineStore && window.offlineStore.purgeAll) {
                await window.offlineStore.purgeAll();
            }
        } catch (_e) {
            // ignore — siehe oben.
        }
    }

    // Refs #1828: Endpunkt-Literal zentral in url-patterns.js (ENDPOINTS);
    // base.html/offline_base.html laden url-patterns.js vor diesem Modul.
    function _bundleUrl(clientPk) {
        return window.URL_PATTERNS.ENDPOINTS.clientBundle(clientPk);
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

        // Refs #2067: VOR allen anderen Zweigen — die Archivierung der
        // Einrichtung schlaegt jede feinere Unterscheidung, und sie betrifft
        // nicht diesen einen Klienten, sondern den ganzen lokalen Bestand.
        if (_isFacilityArchived(response)) {
            await _wipeAllOfflineState();
            return "archived";
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
        let archived = false;
        for (const row of rows) {
            const result = await revalidateCachedClient(row.pk);
            if (result === "archived") {
                // Refs #2067: der erste Treffer hat den GESAMTEN lokalen
                // Bestand gewiped — jede weitere Runde liefe gegen bereits
                // geloeschte Zeilen und holte im schlimmsten Fall neue.
                archived = true;
                break;
            }
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
        return {
            purged: purged,
            refreshed: refreshed,
            total: rows.length,
            ratelimited: ratelimited,
            archived: archived,
        };
    }

    /* ─── Refs #1520/#1499 — personenloses Facility-Meta-Bundle ───────────── */

    function _facilityBundleUrl() {
        return window.URL_PATTERNS.ENDPOINTS.FACILITY_BUNDLE;
    }

    /*
     * Refs #1520/#1499: Spiegel von `_isSchemaMismatch` fuer die EIGENE
     * Facility-Schema-Version. Fail-closed: ein fehlender/`undefined` Wert
     * gilt als Mismatch (kein automatisches Gueltig).
     */
    function _isFacilitySchemaMismatch(schemaVersion) {
        return schemaVersion !== FACILITY_BUNDLE_SCHEMA_VERSION;
    }

    /*
     * Refs #1520/#1499: Personenloses Facility-Meta-Bundle vom Server
     * speichern (Analog `saveClientBundle`, aber ein Singleton ohne
     * Roster/PII):
     *   {schema_version, generated_at, ttl, expires_at, document_types,
     *    assignable_users, tally}
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
            // #1912: Tally-Metadaten + bestaetigte Counts bleiben im selben
            // AES-GCM-Umschlag wie der Facility-Katalog. Keine zweite oder
            // Klartext-Ablage.
            tally: bundle.tally || null,
            generatedAt: bundle.generated_at,
            expiresAt: bundle.expires_at,
            ttl: bundle.ttl,
            schemaVersion: bundle.schema_version,
        }, _aad("facility", FACILITY_ROW_KEY));

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
     * Refs #1520/#1499: das gespeicherte Facility-Meta-Bundle lesen (Analog
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
            envelope = await crypto.decryptPayload(row.data, _aad("facility", FACILITY_ROW_KEY));
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
            tally: envelope.tally || null,
            generatedAt: envelope.generatedAt,
            expiresAt: envelope.expiresAt,
            ttl: envelope.ttl,
            schemaVersion: envelope.schemaVersion,
        };
    }

    /*
     * Refs #1520/#1499: das Facility-Meta-Bundle gegen den Server
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

        // Refs #2067: wie in `revalidateCachedClient` — Archivierung der
        // Einrichtung wipet den gesamten lokalen Bestand, nicht nur die
        // Meta-Zeile.
        if (_isFacilityArchived(response)) {
            await _wipeAllOfflineState();
            return "archived";
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

    // Refs #1838: Exporte dieser Datei auf die EINE Fassade (siehe offline-db.js).
    window.offlineStore = Object.assign(window.offlineStore || {}, {
        saveClientBundle: saveClientBundle,
        getOfflineClient: getOfflineClient,
        listOfflineClients: listOfflineClients,
        listOfflineClientsDetailed: listOfflineClientsDetailed,
        // W3 (#1499) — cross-client + anonyme Aggregatoren fuer die
        // Zeitstrom-/Aufgaben-Offline-Shells (/ bzw. /workitems/)
        listOfflineEventsAggregated: listOfflineEventsAggregated,
        listOfflineWorkItemsAggregated: listOfflineWorkItemsAggregated,
        removeOfflineClient: removeOfflineClient,
        isClientOffline: isClientOffline,
        countOfflineClients: countOfflineClients,
        // F-10 (#1110) — Re-Validierung gegen den Server beim Online-Kontakt
        revalidateCachedClient: revalidateCachedClient,
        revalidateCachedClients: revalidateCachedClients,
        hasUnsyncedEventsFor: hasUnsyncedEventsFor,
        countUnsyncedEventsFor: countUnsyncedEventsFor,
        // Refs #1520/#1499 — personenloses Facility-Meta-Bundle
        saveFacilityBundle: saveFacilityBundle,
        getOfflineFacility: getOfflineFacility,
        revalidateCachedFacility: revalidateCachedFacility,
    });
})();
