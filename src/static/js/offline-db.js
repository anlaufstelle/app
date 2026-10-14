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
 *
 * Schema v5 (Refs #1476, R9): `occurredAt` faellt aus dem events-Index und aus
 * dem Plaintext-Row-Feld (unverschluesselte PII-Verknuepfungsmetadaten, nie per
 * Index abgefragt). Der Zeitstempel lebt weiter im verschluesselten
 * `data`-Envelope. `.upgrade` strippt das Feld auch aus Bestands-Rows.
 *   events: pk, clientPk, localStatus
 *
 * Refs #1838: Diese Datei ist Teil 1/4 des offline-store-Verbunds
 * (offline-db → offline-bundles → offline-revocations → offline-store;
 * Script-Tag-Reihenfolge fix, siehe base.html/offline_base.html). Sie traegt
 * Dexie-Schema, Krypto-Anbindung, die verschluesselten Primitive und den
 * TTL-Purge. window.offlineStore bleibt EINE Fassade — jede Datei haengt
 * ihre Exporte per Object.assign daran. Die privaten Helfer wandern ueber
 * einen VERBRAUCHBAREN internen Kanal (window.__offlineStoreInternals) an
 * die Schwester-Module: je Modul eine Abholung, danach null, die letzte
 * Datei raeumt ab — ein dauerhafter Fassaden-Export der Internals waere
 * exakt das Entschluesselungs-Orakel, das die R8-Haertung (#1476)
 * geschlossen hat. Primaere Kontrolle bleibt die script-src-'self'-CSP.
 */
(function () {
    "use strict";


    const DB_NAME = "anlaufstelle-offline";
    // Refs #1520/#1499: `facility` ist Teil von TABLES, damit purgeAll
    // (Logout/Passwortwechsel) das personenlose Meta-Bundle mitloescht.
    const TABLES = ["queue", "drafts", "meta", "clients", "cases", "events", "facility"];
    const TTL_MS = 48 * 60 * 60 * 1000; // 48h
    const MAX_OFFLINE_CLIENTS = 20;
    // F-05 (#1425, ADR-022): muss mit BUNDLE_SCHEMA_VERSION in
    // core/services/system/offline.py synchron gehalten werden (kein
    // Build-Sync, analog TTL_MS/BUNDLE_TTL_SECONDS oben) -- ein Mismatch
    // zwingt den Lesepfad zum Purge, siehe _isSchemaMismatch.
    // Refs #1529/#1499: v1->v2 (client.lastContact neu im Bundle) -- Alt-v1-
    // Bundles fallen dadurch im getOfflineClient/listOfflineClientsDetailed-
    // Gate raus und werden gepurged.
    const BUNDLE_SCHEMA_VERSION = 2;
    // Refs #1520/#1499: eigene Schema-Version fuers personenlose
    // Facility-Meta-Bundle -- UNABHAENGIG von BUNDLE_SCHEMA_VERSION (Klient),
    // damit beide Bundles getrennt evolvieren koennen. Muss mit
    // FACILITY_BUNDLE_SCHEMA_VERSION in core/services/system/offline.py
    // synchron bleiben (kein Build-Sync, wie BUNDLE_SCHEMA_VERSION). Ein
    // Mismatch zwingt den Lesepfad zum Purge, siehe _isFacilitySchemaMismatch.
    // #1912: v1->v2 fuegt den policy-gefilterten Tally-Snapshot hinzu. Der
    // bestehende Lesepfad verwirft Alt-v1 fail-closed.
    const FACILITY_BUNDLE_SCHEMA_VERSION = 2;
    // Refs #1520/#1499: das Facility-Bundle ist ein Singleton (genau eine
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
    // Refs #1520/#1499: additiver Upgrade — neue `facility`-Tabelle fuers
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
    // Refs #1476 (R9): `occurredAt` (Ereignis-Zeitstempel) faellt aus dem
    // events-Index UND aus dem Plaintext-Row-Feld — es war unverschluesselte
    // PII-Verknuepfungsmetadaten (wann fand welcher Kontakt statt) und wurde
    // nie per Index abgefragt (`.where("occurredAt")` existiert nicht). Der
    // Zeitstempel steckt ohnehin im verschluesselten `data`-Envelope (clean:
    // `occurred_at`; Edit/New: `occurredAt`) und wird von dort gerendert
    // (`normalizeOfflineEventRecord`). Die `.upgrade`-Modify entfernt das
    // Plaintext-Feld auch aus BESTEHENDEN Rows (reine Plaintext-Aenderung,
    // kein Decrypt/Re-Encrypt, der verschluesselte `data`-Envelope bleibt
    // unangetastet). `pk` (Primaerschluessel) und `clientPk` (Fremdschluessel-
    // Index fuer per-Klient-Operationen) bleiben — sie sind funktional
    // zwingend und opake Zufalls-UUIDs.
    db.version(5)
        .stores({
            queue: "++id, url, createdAt, retryAfter, localStatus",
            drafts: "formKey, updatedAt",
            meta: "key",
            clients: "pk, lastSynced",
            cases: "pk, clientPk, lastSynced",
            events: "pk, clientPk, localStatus",
            facility: "key",
        })
        .upgrade((tx) =>
            tx
                .table("events")
                .toCollection()
                .modify((row) => {
                    delete row.occurredAt;
                })
        );

    // Refs #1476 (R8): Die Krypto-Primitive per Einmal-Handshake beziehen und
    // cachen — crypto.js stellt encrypt/decrypt nicht mehr auf der
    // oeffentlichen `window.crypto_session`-Oberflaeche bereit. Faellt `_link`
    // (aeltere crypto.js oder ein Test-Mock, der encrypt/decrypt direkt auf das
    // Objekt legt), wird die Oberflaeche direkt genutzt.
    let _cryptoImpl = null;
    function _crypto() {
        if (_cryptoImpl) return _cryptoImpl;
        const cs = window.crypto_session;
        if (!cs) {
            throw new Error("CryptoSessionNotLoaded");
        }
        if (typeof cs._link === "function") {
            const primitives = cs._link();
            if (!primitives) {
                // Der Einmal-Handshake wurde bereits konsumiert — darf nur EINEN
                // legitimen Konsumenten geben (dieses Modul). Laut aufschlagen
                // statt still ein kaputtes Krypto zu benutzen.
                throw new Error("CryptoPrimitivesAlreadyConsumed");
            }
            _cryptoImpl = primitives;
        } else {
            _cryptoImpl = cs;
        }
        return _cryptoImpl;
    }

    /*
     * Refs #1476 (R7): AES-GCM-AAD-Kontext fuer einen Record. Bindet jeden
     * Umschlag an seine logische Identitaet (Tabelle + Primaerschluessel), damit
     * ein Chiffrat nicht innerhalb von IndexedDB auf einen anderen Record/eine
     * andere Tabelle umgezogen werden kann (Umzug/Rollback-Schutz). Der
     * ``queue``-Primaerschluessel ist ``++id`` (autoincrement) und steht beim
     * Ersteinreihen noch nicht fest; ein Zwei-Phasen-Write scheidet aus, weil
     * ein WebCrypto-``await`` innerhalb einer Dexie-rw-Transaktion diese
     * vorzeitig schliesst (#1414). Die generische Replay-Queue wird daher nur an
     * ihre TABELLE gebunden — ihre Rows sind selbstbeschreibende Requests, die
     * beim Replay serverseitig neu autorisiert werden (Restposten in #1476
     * dokumentiert). Alle PII-tragenden Tabellen (clients/cases/events/facility/
     * drafts/meta) binden Tabelle + Primaerschluessel.
     */
    function _aad(table, primaryKey) {
        if (table === "queue") return "queue";
        return table + ":" + String(primaryKey);
    }

    function _primKeyName(table) {
        return db[table].schema.primKey.name;
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
        return (
            e &&
            (e.name === "NoSessionKeyError" ||
                e.message === "NoSessionKey" ||
                // Refs #1476 (R7): Ein v1-Umschlag ohne mitgegebenen AAD-Kontext
                // ist ein Programmierfehler an einer neuen Aufrufstelle — als
                // TRANSIENT behandeln (Row NICHT verwerfen), damit ein
                // uebersehener Kontext keinen stillen Datenverlust ausloest,
                // sondern in Tests/Read als null auffaellt.
                e.name === "AadRequiredError" ||
                e.message === "AadRequired")
        );
    }

    // Refs R1 (Sicherheitsreview 2026-07-05): Ungesyncte Arbeit — jede
    // `queue`-Row und `events` mit `localStatus` in {new, modified, conflict,
    // dead}. Nur diese darf bei einem PERMANENTEN Decrypt-Fehler nicht STILL
    // verschwinden; rein gecachte, serverseitig erneut abrufbare Read-Kopien
    // (clients/cases/drafts/synced events) bleiben beim #576-Auto-Discard.
    // Refs #1838 (Review): eingefroren — seit der Konsolidierung ist das
    // EINE geteilte Liste zweier Module; niemand darf sie mutieren.
    const _UNSYNCED_EVENT_STATUS = Object.freeze(["new", "modified", "conflict", "dead"]);

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

    async function putEncrypted(table, record, expectedDraftJson) {
        const crypto = _crypto();
        const { data, ...rest } = record;
        // Refs #1476 (R7): an Tabelle + Primaerschluessel binden. Fuer die
        // ``queue`` bindet ``_aad`` tabellenweit (id noch unbekannt); fuer alle
        // anderen Tabellen traegt ``rest`` den Primaerschluessel bereits.
        const aad = _aad(table, rest[_primKeyName(table)]);
        const envelope = await crypto.encryptPayload(data, aad);
        // Refs #2561: autosave may only replace the snapshot it opened or
        // last wrote, or recreate a missing draft after new input. Compare
        // under the same write lock as the replacement.
        if (table === "drafts" && expectedDraftJson !== undefined) {
            return db.transaction("rw", db.drafts, async () => {
                const current = await db.drafts.get(rest.formKey);
                const currentData = current ? await Dexie.waitFor(
                    crypto.decryptPayload(current.data, aad), 3000
                ) : null;
                if (current && JSON.stringify(currentData) !== expectedDraftJson) return false;
                await db.drafts.put({ ...rest, data: envelope });
                return true;
            });
        }
        return db[table].put({ ...rest, data: envelope });
    }

    async function getDecrypted(table, primaryKey) {
        const crypto = _crypto();
        const row = await db[table].get(primaryKey);
        if (!row) return null;
        try {
            row.data = await crypto.decryptPayload(row.data, _aad(table, primaryKey));
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

    async function listDecrypted(table, filterFn, keyPrefix) {
        const crypto = _crypto();
        const pkName = _primKeyName(table);
        // Mixed metadata contains plaintext control records. Restrict the
        // key range before decrypting or considering any cleanup.
        const rows = await (keyPrefix === undefined ? db[table] :
            db[table].where(pkName).startsWith(keyPrefix)).toArray();
        const out = [];
        for (const row of rows) {
            try {
                const decrypted = {
                    ...row,
                    // Refs #1476 (R7): mit demselben Kontext (Tabelle + pk)
                    // entschluesseln, mit dem geschrieben wurde.
                    data: await crypto.decryptPayload(row.data, _aad(table, row[pkName])),
                };
                if (!filterFn || filterFn(decrypted)) {
                    out.push(decrypted);
                }
            } catch (e) {
                // A scoped confirmation read must report uncertainty without
                // dropping the evidence or authorizing automatic recovery.
                if (keyPrefix !== undefined) throw e;
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

    // Refs #2561: compare and delete under one IndexedDB write lock.
    // Keep the transaction alive during WebCrypto; errors retain the draft.
    async function deleteConfirmedDraft(formKey, token) {
        if (!token) return false;
        return db.transaction("rw", db.drafts, async () => {
            const row = await db.drafts.get(formKey);
            if (!row) return false;
            const data = await Dexie.waitFor(_crypto().decryptPayload(row.data, _aad("drafts", formKey)), 3000);
            if (data.__submissionToken !== token) return false;
            await db.drafts.delete(formKey);
            return true;
        });
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
                envelope = await crypto.decryptPayload(row.data, _aad("clients", row.pk));
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
                // Refs #1838: removeOfflineClient lebt in offline-bundles.js — Aufloesung
                // zur Callzeit ueber die Fassade (Duck-Typing-Muster des Bestands;
                // der Aufruf passiert nie waehrend des Parse).
                await window.offlineStore.removeOfflineClient(row.pk, { force: true });
                continue;
            }
            if (_isExpired(envelope.expiresAt, ts)) {
                // Refs #1353: TTL-Ablauf ist KEIN Loeschgrund fuer unsynced
                // Arbeit — ohne force bleiben modified/new/conflict/dead
                // Events erhalten, nur der Server-Spiegel (clients/cases/
                // clean-events) faellt weg.
                await window.offlineStore.removeOfflineClient(row.pk);
            }
        }
    }

    // Refs #2528: absent/invalid expiry is expired at both read and list gates.
    function _isExpired(expiresAt, now) {
        if (typeof expiresAt !== "string" || !expiresAt) return true;
        const exp = Date.parse(expiresAt);
        if (!Number.isFinite(exp)) return true;
        return exp <= (now ?? Date.now());
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
     * Refs #1760: winziger, UNVERSCHLUESSELTER Key-Value-Zugriff auf
     * die `meta`-Tabelle — wie `storagePersist` bewusst im Klartext: die Werte
     * sind reine Steuer-Metadaten (Booleans/Zeitstempel) ohne jeden Personenbezug,
     * dafuer lohnt sich kein Crypto-Overhead, und sie muessen auch OHNE
     * Session-Key lesbar sein (der Boot-Drossel-Zeitstempel wird gelesen, bevor
     * feststeht, ob ein Schluessel vorliegt).
     *
     * `purgeAll` (Logout/Passwortwechsel) leert `meta` mit — gewollt: nach einem
     * Logout soll der naechste Boot wieder pruefen duerfen.
     */
    async function getMetaValue(key) {
        const row = await db.meta.get(key);
        return row ? row.value : null;
    }

    async function setMetaValue(key, value) {
        return db.meta.put({ key: key, value: value });
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
     * Refs #2060: True, wenn `e` ein `VersionError` ist — IndexedDB antwortet
     * damit auf den Versuch, eine Datenbank zu oeffnen, deren gespeicherte
     * Version HOEHER ist als die im Code deklarierte. Praktischer Anlass: ein
     * Rollback-Deploy (oder eine zweite, aeltere Instanz auf derselben
     * Origin). Struktur wie bei `_isQuotaError` — je nach Pfad steht der Name
     * direkt auf `e` oder im von Dexie gekapselten `e.inner`.
     */
    function _isVersionError(e) {
        if (!e) return false;
        if (e.name === "VersionError") return true;
        return Boolean(e.inner && e.inner.name === "VersionError");
    }

    /*
     * Refs #2060: Die Meldung fuer genau diesen Zustand. Ohne sie rejecten
     * schlicht ALLE Store-Operationen, die Offline-Daten liegen unerreichbar
     * auf dem Geraet, und die Nutzerin sieht je nach Aufrufer nichts oder
     * etwas Kryptisches — obwohl der Zustand trivial heilbar ist (Update
     * ziehen). Der Text kommt wie beim Service-Worker-Toast als
     * `data-i18n-*`-Attribut vom `<body>` (base.html, Refs #1412); die Form
     * ist bewusst dieselbe wie dort (fixierter Toast unten rechts,
     * wegklickbar), damit zwei Update-Hinweise nicht wie zwei verschiedene
     * Dinge aussehen. `role="alert"` statt `status`: hier ist die Anwendung
     * offline unbrauchbar, das darf nicht bis zum naechsten Vorlesen warten.
     */
    const UPDATE_TOAST_ID = "offline-db-update-toast";
    let _updateRequiredReported = false;

    /*
     * Refs #2060: Deutscher Rueckfall-Text — dokumentierte Ausnahme nach dem
     * Muster von htmx-errors.js (`GENERIC`/`RATE_LIMITED`): eine statisch
     * ausgelieferte Datei kennt die Sprache der Nutzerin nicht.
     *
     * Der Regelfall bleibt das uebersetzte Template-Label; es GEWINNT, wo es
     * existiert — und das tut es in drei der fuenf Layouts, die offline-db.js
     * laden: `base.html`, `auth/login.html`, `auth/password_change.html`.
     *
     * Ohne Label bleiben genau die beiden PRECACHTEN: der terminale
     * Offline-Fallback (`offline.html`) und die In-Place-Shells
     * (`offline_base.html`). Beide stehen im Shell-Hash-Guard (Refs
     * #1575/#1845) — dort kostet ein Attribut einen CACHE_NAME-Bump, also eine
     * release-weite Entscheidung. Bis dahin traegt der Rueckfall die Meldung.
     *
     * Genau dort ist die Meldung am noetigsten: Ein Versionskonflikt macht die
     * Anwendung offline unbrauchbar, und der Zustand ist trivial heilbar —
     * aber nur, wenn jemand es sagt. Schweigen war der Zustand VOR diesem Fix.
     * Der Text hier ist wortgleich mit dem msgid in base.html; ein Guard
     * (src/tests/test_i18n_offline_db_update.py) haelt beide zusammen.
     */
    const UPDATE_REQUIRED_FALLBACK =
        "Die lokal gespeicherten Daten stammen aus einer neueren Programmversion. " +
        "Bitte die Seite neu laden, um die Anwendung zu aktualisieren.";
    const DISMISS_FALLBACK = "Schließen";

    function _showUpdateRequiredToast() {
        if (!document.body || document.getElementById(UPDATE_TOAST_ID)) return;
        const label = document.body.dataset.i18nDbUpdateRequired || UPDATE_REQUIRED_FALLBACK;
        // Der Abweis-Text: eigener Schluessel zuerst, sonst der des
        // Service-Worker-Toasts (gleicher msgid, auf den meisten Layouts schon
        // da), sonst der Rueckfall. Der eigene Schluessel existiert fuer
        // Layouts OHNE Worker-Toast — dort waere dessen Attributname falsch.
        const dismissLabel =
            document.body.dataset.i18nDbUpdateDismiss || document.body.dataset.i18nSwDismiss || DISMISS_FALLBACK;

        const toast = document.createElement("div");
        toast.id = UPDATE_TOAST_ID;
        toast.setAttribute("role", "alert");
        toast.className =
            "fixed bottom-4 right-4 z-50 max-w-sm bg-red-600 text-white " +
            "rounded-lg shadow-lg p-4 flex items-center gap-3";
        // Refs #2387 (Muster #1386): sichtbarkeitskritische Eigenschaften
        // zusaetzlich inline — fehlen die nur hier verwendeten Klassen in der
        // ausgelieferten styles.css, laege der Toast sonst unter dem
        // Seitenende. Die Inline-Werte sind bewusst fuehrend und entsprechen
        // den Klassenwerten (bottom-4/right-4 = 1rem, z-50; Farben ueber die
        // Theme-Variablen von bg-red-600/text-white, Hex nur als Rueckfall,
        // etwa auf Seiten ohne styles.css); die Klassen tragen die uebrige
        // Gestaltung.
        toast.style.position = "fixed";
        toast.style.bottom = "1rem";
        toast.style.right = "1rem";
        toast.style.zIndex = "50";
        toast.style.backgroundColor = "var(--color-red-600, #dc2626)"; // bg-red-600
        toast.style.color = "var(--color-white, #fff)"; // text-white

        const text = document.createElement("span");
        text.className = "text-sm";
        text.textContent = label;
        toast.appendChild(text);

        const dismissBtn = document.createElement("button");
        dismissBtn.type = "button";
        dismissBtn.className = "text-sm underline";
        dismissBtn.textContent = dismissLabel;
        dismissBtn.addEventListener("click", function () {
            toast.remove();
        });
        toast.appendChild(dismissBtn);

        document.body.appendChild(toast);
    }

    function _reportUpdateRequired() {
        if (_updateRequiredReported) return;
        _updateRequiredReported = true;
        // eslint-disable-next-line no-console
        console.error(
            "[offline-store] Die lokale Datenbank ist neuer als dieser Programmstand " +
                "(VersionError) — App-Update erforderlich."
        );
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", _showUpdateRequiredToast, { once: true });
        } else {
            _showUpdateRequiredToast();
        }
        window.dispatchEvent(new CustomEvent("offline-db-update-required"));
    }

    /*
     * Refs #2060: Das Oeffnen ist der EINE Ort, an dem der Versionskonflikt
     * entsteht — hier abgefangen statt in acht Aufrufern erraten. Dexie
     * oeffnet ohnehin lazy bei der ersten Operation, und die faellt auf jeder
     * Seite an (sync-orchestrator.js prueft beim Start Zaehler); das
     * vorgezogene `open()` legt also keine Datenbank an, die nicht ohnehin
     * angelegt wuerde. Andere Oeffnungsfehler werden hier bewusst nur
     * verschluckt, damit keine unbehandelte Rejection entsteht — sie
     * erreichen ihre Aufrufer unveraendert ueber deren eigene Operation.
     */
    db.open().catch(function (e) {
        if (_isVersionError(e)) _reportUpdateRequired();
    });

    // Refs #1838: EINE Fassade — hier angelegt, die Schwester-Module haengen
    // ihre Exporte per Object.assign daran.
    window.offlineStore = Object.assign(window.offlineStore || {}, {
        db: db,
        putEncrypted: putEncrypted,
        getDecrypted: getDecrypted,
        listDecrypted: listDecrypted,
        deleteRow: deleteRow,
        deleteConfirmedDraft: deleteConfirmedDraft,
        purgeAll: purgeAll,
        purgeExpired: purgeExpired,
        purgeExpiredBundles: purgeExpiredBundles,
        count: count,
        // Refs #1760 — unverschluesselte Steuer-Metadaten (kein PII, kein Key noetig)
        getMetaValue: getMetaValue,
        setMetaValue: setMetaValue,
        TTL_MS: TTL_MS,
        MAX_OFFLINE_CLIENTS: MAX_OFFLINE_CLIENTS,
        // F-05 (#1425) — Lesepfad-Gate vergleicht dagegen, siehe _isSchemaMismatch
        BUNDLE_SCHEMA_VERSION: BUNDLE_SCHEMA_VERSION,
        // Refs #1520/#1499 — Facility-Meta-Bundle-Gate, siehe _isFacilitySchemaMismatch
        FACILITY_BUNDLE_SCHEMA_VERSION: FACILITY_BUNDLE_SCHEMA_VERSION,
    });

    // Refs #1838: Verbrauchbarer interner Kanal fuer die Schwester-Module —
    // Muster crypto_session._link() (R8, #1476): take() liefert die privaten
    // Helfer je Abholung genau einmal. Der Kanal entwaffnet sich DREIFACH
    // (Integrationsfenster-Review #1838 — das fruehere alleinige Abraeumen
    // durch offline-store.js liess den Slot samt _crypto-Orakel unbefristet
    // auf window liegen, wenn genau DIESE Datei nicht laedt):
    //   1. take() schliesst bei erschoepftem Budget selbst ab (loescht den
    //      window-Slot);
    //   2. budget-unabhaengig entwaffnet DOMContentLoaded (+ pagehide) —
    //      der Parser feuert es erst, NACHDEM alle parser-blockierenden
    //      <script src>-Tags ausgefuehrt sind. Ein setTimeout(0) traegt diese
    //      Garantie NICHT: beim Warten auf ein noch nicht bereites
    //      Schwester-Script spinnt der Parser das Event-Loop und fuehrt
    //      faellige Timer AUS (WHATWG-HTML); unter dem cache-first-SW der
    //      Offline-Seiten empirisch reproduziert — der 0ms-Disarm feuerte
    //      ZWISCHEN den Tags und entwaffnete den Slot vor den Abholern
    //      (Verify-Pass zu c6d451bd). Ausserhalb eines laufenden Parses
    //      (readyState nicht "loading", z.B. Test-Harness via dynamic
    //      import) bleibt der Makrotask-Fallback;
    //   3. offline-store.js loescht als letzte Datei zusaetzlich (Guertel +
    //      Hosentraeger, deckt exotische Timer-Unterdrueckung ab).
    // Ein spaeter injiziertes Skript findet weder den Slot noch die
    // Primitive; die Fassade selbst traegt NIE Krypto-Interna (vitest-Pins:
    // offline-store-split.test.mjs). Der Slot traegt NUR die 14 von den
    // Schwestern tatsaechlich destrukturierten Schluessel und ist
    // eingefroren — wer ihn abgreift, bekommt nicht mehr als noetig und
    // kann ihn nicht als Schmuggelpfad mutieren.
    // _TAKE_BUDGET == Anzahl der Schwester-Module (Refs #1838-Schnittplan).
    const _TAKE_BUDGET = 3;
    let _takesLeft = _TAKE_BUDGET;
    let _internals = Object.freeze({
        db,
        _crypto,
        _aad,
        _isTransientDecryptError,
        _isExpired,
        _isSchemaMismatch,
        _isQuotaError,
        _UNSYNCED_EVENT_STATUS,
        FACILITY_ROW_KEY,
        FACILITY_BUNDLE_SCHEMA_VERSION,
        putEncrypted,
        getDecrypted,
        listDecrypted,
        purgeExpired,
    });

    function _disarmInternals() {
        _internals = null;
        _takesLeft = 0;
        delete window.__offlineStoreInternals;
    }

    window.__offlineStoreInternals = {
        take() {
            if (_takesLeft <= 0 || _internals === null) return null;
            _takesLeft -= 1;
            const handed = _internals;
            if (_takesLeft === 0) _disarmInternals();
            return handed;
        },
    };
    if (document.readyState === "loading") {
        // Browser-Pfad: siehe Punkt 2 der Entwaffnungs-Doku oben.
        document.addEventListener("DOMContentLoaded", _disarmInternals, { once: true });
        window.addEventListener("pagehide", _disarmInternals, { once: true });
    } else {
        // Nicht-Parser-Kontext (Test-Harness laedt per dynamic import bei
        // readyState "complete"): Makrotask-Fallback. Seit #1994 erzwingt der
        // Verbund-Harness tests/js/_offline_store_modules.mjs den echten
        // DCL-Pfad (readyState "loading"); diesen Fallback nimmt nur noch
        // loadPartial() in tests/js/offline-store-split.test.mjs.
        window.setTimeout(_disarmInternals, 0);
    }
})();
