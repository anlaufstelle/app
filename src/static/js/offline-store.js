/*
 * Encrypted-at-rest IndexedDB store for the offline mode.
 *
 * Wraps Dexie.js with a thin envelope that runs every payload through
 * window.crypto_session.encryptPayload before write and decryptPayload after
 * read. If decryption fails (key was wiped, salt rotated, password changed),
 * the offending row is silently dropped — that is the auto-discard behaviour
 * specified for #573 / #576.
 *
 * Schema v1:
 *   queue:  ++id, url, createdAt, lastError, retryAfter, attempts
 *   drafts: formKey, updatedAt
 *   meta:   key
 * Every record's `data` field is { iv, ct } (never plaintext).
 */
(function () {
    "use strict";

    const DB_NAME = "anlaufstelle-offline";
    const TABLES = ["queue", "drafts", "meta"];
    const TTL_MS = 48 * 60 * 60 * 1000; // 48h

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

    window.offlineStore = {
        db: db,
        putEncrypted: putEncrypted,
        getDecrypted: getDecrypted,
        listDecrypted: listDecrypted,
        deleteRow: deleteRow,
        purgeAll: purgeAll,
        purgeExpired: purgeExpired,
        count: count,
        TTL_MS: TTL_MS,
    };
})();
