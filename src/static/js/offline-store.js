/*
 * Encrypted-at-rest IndexedDB store for the offline mode.
 *
 * Wraps Dexie.js with a thin envelope that runs every payload through
 * window.crypto_session.encryptPayload before write and decryptPayload after
 * read. If decryption fails (key was wiped, salt rotated, password changed),
 * the offending row is silently dropped — that is the auto-discard behaviour
 * specified for #573 / #576.
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
 */
(function () {
    "use strict";

    const DB_NAME = "anlaufstelle-offline";
    const TABLES = ["queue", "drafts", "meta", "clients", "cases", "events"];
    const TTL_MS = 48 * 60 * 60 * 1000; // 48h
    const MAX_OFFLINE_CLIENTS = 20;

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

    function _crypto() {
        if (!window.crypto_session) {
            throw new Error("CryptoSessionNotLoaded");
        }
        return window.crypto_session;
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
        } catch (_e) {
            // Auto-discard on decrypt failure (salt/password rotated)
            await db[table].delete(primaryKey);
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
            } catch (_e) {
                // Auto-discard
                await db[table].delete(row[db[table].schema.primKey.name]);
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
        const cutoff = (now || Date.now()) - TTL_MS;
        // Queue records with createdAt < cutoff
        await db.queue.where("createdAt").below(cutoff).delete();
        // Drafts with updatedAt < cutoff
        await db.drafts.where("updatedAt").below(cutoff).delete();
    }

    async function count(table) {
        return db[table].count();
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
    async function saveClientBundle(bundle) {
        const crypto = _crypto();
        const pk = bundle.client && bundle.client.pk;
        if (!pk) throw new Error("MalformedBundle");
        const now = Date.now();

        // Remove stale per-client state in case of a re-sync.
        await removeOfflineClient(pk);

        await db.clients.put({
            pk: pk,
            lastSynced: now,
            data: await crypto.encryptPayload({
                client: bundle.client,
                document_types: bundle.document_types || [],
                workitems: bundle.workitems || [],
                generatedAt: bundle.generated_at,
                expiresAt: bundle.expires_at,
                ttl: bundle.ttl,
                schemaVersion: bundle.schema_version,
            }),
        });

        for (const caseRec of bundle.cases || []) {
            await db.cases.put({
                pk: caseRec.pk,
                clientPk: pk,
                lastSynced: now,
                data: await crypto.encryptPayload(caseRec),
            });
        }

        for (const event of bundle.events || []) {
            await db.events.put({
                pk: event.pk,
                clientPk: pk,
                occurredAt: event.occurred_at,
                localStatus: "clean",
                data: await crypto.encryptPayload(event),
            });
        }
    }

    async function getOfflineClient(pk) {
        const crypto = _crypto();
        const row = await db.clients.get(pk);
        if (!row) return null;
        let envelope;
        try {
            envelope = await crypto.decryptPayload(row.data);
        } catch (_e) {
            await db.clients.delete(pk);
            return null;
        }

        const cases = await listDecrypted("cases", (r) => r.clientPk === pk);
        const events = await listDecrypted("events", (r) => r.clientPk === pk);

        return {
            pk: pk,
            lastSynced: row.lastSynced,
            client: envelope.client,
            documentTypes: envelope.document_types,
            workitems: envelope.workitems,
            generatedAt: envelope.generatedAt,
            expiresAt: envelope.expiresAt,
            ttl: envelope.ttl,
            schemaVersion: envelope.schemaVersion,
            cases: cases.map((r) => r.data),
            // Surface the indexed `localStatus` alongside the decrypted
            // payload so the offline-detail template can badge unsynced
            // or conflicting edits without another IndexedDB round-trip.
            events: events
                .map((r) => {
                    const payload = r.data && r.data.pk ? r.data : { ...(r.data || {}), pk: r.pk };
                    return { ...payload, localStatus: r.localStatus || "clean" };
                })
                .sort((a, b) => (a.occurred_at < b.occurred_at ? 1 : -1)),
        };
    }

    async function listOfflineClients() {
        // Return a thin list (pk + lastSynced) without decrypting every
        // bundle — the detail view decrypts on demand.
        const rows = await db.clients.toArray();
        return rows.map((r) => ({ pk: r.pk, lastSynced: r.lastSynced }));
    }

    async function removeOfflineClient(pk) {
        await db.clients.delete(pk);
        await db.cases.where("clientPk").equals(pk).delete();
        await db.events.where("clientPk").equals(pk).delete();
    }

    async function isClientOffline(pk) {
        return (await db.clients.where("pk").equals(pk).count()) > 0;
    }

    async function countOfflineClients() {
        return db.clients.count();
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
        // Dexie where() on indexed field `localStatus`:
        return db.events
            .where("localStatus")
            .anyOf(["modified", "new", "conflict"])
            .count();
    }

    async function countConflictEvents() {
        return db.events.where("localStatus").equals("conflict").count();
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
        } catch (_e) {
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

    window.offlineStore = {
        db: db,
        putEncrypted: putEncrypted,
        getDecrypted: getDecrypted,
        listDecrypted: listDecrypted,
        deleteRow: deleteRow,
        purgeAll: purgeAll,
        purgeExpired: purgeExpired,
        count: count,
        saveClientBundle: saveClientBundle,
        getOfflineClient: getOfflineClient,
        listOfflineClients: listOfflineClients,
        removeOfflineClient: removeOfflineClient,
        isClientOffline: isClientOffline,
        countOfflineClients: countOfflineClients,
        // Stage 3 (#575) — offline edit + conflict tracking
        saveOfflineEdit: saveOfflineEdit,
        getOfflineEvent: getOfflineEvent,
        listModifiedEvents: listModifiedEvents,
        listConflicts: listConflicts,
        countUnsyncedEvents: countUnsyncedEvents,
        countConflictEvents: countConflictEvents,
        updateEventLocalStatus: updateEventLocalStatus,
        saveConflictState: saveConflictState,
        clearOfflineEdit: clearOfflineEdit,
        TTL_MS: TTL_MS,
        MAX_OFFLINE_CLIENTS: MAX_OFFLINE_CLIENTS,
    };
})();
