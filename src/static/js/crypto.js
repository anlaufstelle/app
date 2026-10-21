/*
 * Client-side AES-GCM-256 session key derived from the login password.
 *
 * The CryptoKey is stored as a non-extractable structured-clone object in
 * IndexedDB (table `meta`, key `sessionKey`). Persistence is required to
 * survive page navigations, and the key also outlives closing the tab, the
 * browser or a device restart. Non-extractable only means the raw key bytes
 * cannot be exported through WebCrypto; browser-profile protection requires
 * device encryption. Until expiry is enforced, any code running in this
 * origin of the browser profile (e.g. on an unlocked device) can still use
 * the key to decrypt the offline store.
 *
 * Lifecycle:
 *   - login.html / password_change.html call deriveSessionKey() once after
 *     successful authentication; the key is stashed in IndexedDB.
 *   - encryptPayload / decryptPayload lazy-load the CryptoKey from IndexedDB
 *     and cache it for the rest of the page's lifetime.
 *   - clearSessionKey() (called on logout / password change) deletes the row.
 *   - The browser's Clear-Site-Data: "storage", "cache" header on logout
 *     drops the entire IndexedDB as defence in depth.
 *   - Idle wipe (Refs #1065): the key lifetime is coupled to the server
 *     session. A throttled lastActivity stamp lives next to the key; once
 *     it is older than the session age (data-session-age on <body>,
 *     fallback 1800 s = SESSION_COOKIE_AGE), the key is withdrawn; unsent
 *     encrypted work is retained (Refs #1324). Checks run on boot, every
 *     crypto operation, a 60 s interval and tab return.
 *   - Absolute cap (Refs #2528): createdAt/expiresAt are stored at derivation
 *     using the server policy (data-offline-key-max-age, seconds). Activity
 *     never renews this cap. Expiry only deletes the key, retaining ciphertext.
 *     Missing/invalid metadata or an observed clock rollback also locks it.
 *     No wipe on pagehide/close: re-opening offline within the idle window
 *     must keep working (Streetwork use case).
 *
 * Error taxonomy (Refs #1352): encryptPayload/decryptPayload throw typed
 * errors (`err.name`) so offline-store.js can tell a TRANSIENT absence of
 * the key apart from a PERMANENT decrypt failure:
 *   - TRANSIENT — `NoSessionKeyError` (message "NoSessionKey"): no key is
 *     loaded right now (Idle-Lock #1324, or a fresh boot before re-login).
 *     The ciphertext is still valid and becomes readable again once the
 *     same password re-derives the same key — callers MUST NOT discard the
 *     row for this error.
 *   - PERMANENT — `InvalidEnvelopeError` (message "InvalidEnvelope") for a
 *     malformed record, and the native WebCrypto `DOMException` with
 *     `name === "OperationError"` that `subtle.decrypt` throws un-wrapped
 *     on a GCM auth-tag mismatch (salt rotated / password changed): the
 *     ciphertext can never be decrypted with the current key — callers
 *     auto-discard the row (#576/F-03).
 *
 * Envelope-Bindung (Refs #1476, R7): encryptPayload/decryptPayload nehmen
 * optional einen `aad`-Kontext (Tabelle + Primaerschluessel, von
 * offline-store.js geliefert). Neue Umschlaege tragen `v: ENVELOPE_VERSION`
 * und binden diesen Kontext als AES-GCM `additionalData` — ein innerhalb von
 * IndexedDB umgezogenes/zurueckgerolltes Chiffrat entschluesselt unter der
 * falschen Identitaet nicht mehr. Bestandsdaten ohne `v` bleiben lesbar.
 *
 * Oberflaechen-Minimierung (Refs #1476, R8): Die Primitive stehen nicht auf
 * dem oeffentlichen `window.crypto_session`, sondern werden per Einmal-
 * Handshake (`_link()`) nur an offline-store.js uebergeben; das oeffentliche
 * Objekt ist eingefroren und non-configurable.
 *
 * Refs #573, #576, #1065, #1352, #1476.
 */
(function () {
    "use strict";

    const PBKDF2_ITERATIONS = 600000;
    const KEY_LENGTH = 256;
    // Refs #1476 (R7): Envelope-Format-Version + AAD-Bindung. Ein AES-GCM-
    // Umschlag {iv, ct} liess sich bislang innerhalb von IndexedDB von einem
    // Record auf einen anderen kopieren (Umzug) oder auf einen aelteren Stand
    // zuruecksetzen (Rollback), OHNE den Schluessel zu kennen — der Lesepfad
    // entschluesselte ihn dann unter falscher Identitaet. Neue Umschlaege
    // tragen `v: ENVELOPE_VERSION` und binden einen von der Aufruferin
    // gelieferten Kontext (Tabelle + Primaerschluessel, siehe offline-store.js
    // `_aad`) als `additionalData` in den GCM-Auth-Tag: ein umgezogenes
    // Chiffrat entschluesselt unter der Ziel-Identitaet nicht mehr. Bestands-
    // Umschlaege OHNE `v` wurden ohne AAD geschrieben und werden weiterhin OHNE
    // AAD entschluesselt (graceful Upgrade, kein Zwangs-Purge); beim naechsten
    // Schreiben erhalten sie die Bindung. Ein spaeteres Strippen des `v`-Felds
    // hilft der Angreiferin nicht: das Chiffrat ist kryptografisch an die AAD
    // gebunden, ein AAD-loser Decrypt eines v1-Umschlags schlaegt fehl.
    const ENVELOPE_VERSION = 1;
    // Separate IndexedDB so the crypto key is not coupled to the Dexie
    // schema version of offline-store.js. Tests that rebuild the offline
    // store would otherwise hit "VersionError" when crypto.js opens v1 of
    // a database that Dexie has already upgraded.
    const DB_NAME = "anlaufstelle-crypto";
    const META_TABLE = "meta";
    const KEY_NAME = "sessionKey";
    const REVOCATION_KEY = "keyRevocation";
    // Idle wipe (Refs #1065): lastActivity stamp in the same meta store.
    const ACTIVITY_KEY = "lastActivity";
    const EXPIRED_KEY = "keyExpired";
    const DEFAULT_KEY_MAX_AGE_S = 12 * 3600; // server model default (Refs #2528)
    const ACTIVITY_THROTTLE_MS = 30 * 1000;
    const IDLE_CHECK_INTERVAL_MS = 60 * 1000;
    const DEFAULT_SESSION_AGE_S = 1800; // fallback = SESSION_COOKIE_AGE

    let cachedKey = null;
    let cachedLifetime = null;
    let keyGeneration = 0; // Invalidates pending key writes and memory hydration on clear (#2528).
    // A synchronous lower bound: a later marker read may update future attempts
    // only. Before initialization, assume no revocation and fail closed if the
    // persisted marker differs; never adopt that newer value for an old attempt.
    let knownRevocation = 0;
    let initialLoad = null; // Promise<void> that resolves once cachedKey reflects IndexedDB
    let initialLoadSettled = false;
    let settleInitialLoad = null; // resolves ``initialLoad``
    let lockedHydration = null; // boot hydration run by a lock holder of this tab
    let lastActivityWrite = 0; // in-memory throttle marker for _touchActivity
    let idleDecision = null; // running idle-window decision of this tab

    function isSupported() {
        return (
            typeof window !== "undefined" &&
            "crypto" in window &&
            "subtle" in window.crypto &&
            typeof window.crypto.subtle.deriveKey === "function" &&
            typeof window.indexedDB !== "undefined"
        );
    }

    function b64UrlToBytes(b64url) {
        let b64 = b64url.replace(/-/g, "+").replace(/_/g, "/");
        const padLen = (4 - (b64.length % 4)) % 4;
        b64 += "=".repeat(padLen);
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i += 1) {
            bytes[i] = bin.charCodeAt(i);
        }
        return bytes;
    }

    function bytesToB64(bytes) {
        let bin = "";
        for (let i = 0; i < bytes.length; i += 1) {
            bin += String.fromCharCode(bytes[i]);
        }
        return btoa(bin);
    }

    function b64ToBytes(b64) {
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i += 1) {
            bytes[i] = bin.charCodeAt(i);
        }
        return bytes;
    }

    // Refs #1476 (R7): Domaenen-getrennte AAD-Bytes aus der Format-Version und
    // dem aufruferseitigen Record-Kontext. Der feste Praefix trennt diese
    // Bindung von jeder anderen (moeglichen) GCM-Nutzung derselben Schluessel-
    // klasse; der Kontext (`aad`) traegt die logische Record-Identitaet.
    function _aadBytes(aad) {
        return new TextEncoder().encode("anlaufstelle-offline/v" + ENVELOPE_VERSION + "|" + aad);
    }

    /*
     * IndexedDB access. We deliberately don't go through Dexie here so this
     * module can be loaded before offline-store.js (and used in a
     * pre-bootstrap context like login.html).
     */
    function _openDb() {
        return new Promise(function (resolve, reject) {
            const req = indexedDB.open(DB_NAME, 1);
            req.onupgradeneeded = function () {
                const db = req.result;
                if (!db.objectStoreNames.contains(META_TABLE)) {
                    db.createObjectStore(META_TABLE, { keyPath: "key" });
                }
            };
            req.onsuccess = function () {
                resolve(req.result);
            };
            req.onerror = function () {
                reject(req.error);
            };
        });
    }

    function _idbGet(key) {
        return _openDb().then(function (db) {
            return new Promise(function (resolve, reject) {
                const tx = db.transaction(META_TABLE, "readonly");
                const store = tx.objectStore(META_TABLE);
                const req = store.get(key);
                req.onsuccess = function () {
                    db.close();
                    resolve(req.result || null);
                };
                req.onerror = function () {
                    db.close();
                    reject(req.error);
                };
            });
        });
    }

    function _noSessionKeyError() {
        const error = new Error("NoSessionKey");
        error.name = "NoSessionKeyError";
        return error;
    }

    function _assertKeyGeneration(generation) {
        if (generation !== keyGeneration) throw _noSessionKeyError();
    }

    function _observeRevocation(row) {
        const current = row ? row.generation : 0;
        if (Number.isSafeInteger(current)) {
            knownRevocation = Math.max(knownRevocation, current);
        }
        return current;
    }

    // Capture before the first async step of login/demo, not after the salt
    // response. The persistent marker also invalidates work in other tabs.
    function beginKeyDerivation() {
        const revocation = knownRevocation;
        return {
            generation: keyGeneration,
            revocation: _idbGet(REVOCATION_KEY).then(
                function (row) {
                    const current = _observeRevocation(row);
                    return current === revocation
                        ? { value: revocation }
                        : { error: _noSessionKeyError() };
                },
                function (error) { return { error }; }
            ),
        };
    }

    function _idbPut(record, generation, revocation) {
        return _openDb().then(function (db) {
            if (generation !== undefined && generation !== keyGeneration) {
                db.close();
                _assertKeyGeneration(generation);
            }
            return new Promise(function (resolve, reject) {
                const tx = db.transaction(META_TABLE, "readwrite");
                const store = tx.objectStore(META_TABLE);
                let failure;
                if (revocation !== undefined) {
                    // Check and write atomically against deletion in any tab.
                    const req = store.get(REVOCATION_KEY);
                    req.onsuccess = function () {
                        const current = _observeRevocation(req.result);
                        if (current !== revocation || generation !== keyGeneration) {
                            failure = _noSessionKeyError();
                            tx.abort();
                            return;
                        }
                        store.put(record);
                    };
                } else {
                    store.put(record);
                }
                tx.oncomplete = function () {
                    db.close();
                    resolve();
                };
                tx.onerror = tx.onabort = function () {
                    db.close();
                    reject(failure || tx.error || new Error("KeyWriteTransactionAborted"));
                };
            });
        });
    }

    function _idbDelete(key, revoke) {
        return _openDb().then(function (db) {
            return new Promise(function (resolve, reject) {
                const tx = db.transaction(META_TABLE, "readwrite");
                const store = tx.objectStore(META_TABLE);
                let revokedGeneration;
                store.delete(key);
                if (revoke) {
                    const req = store.get(REVOCATION_KEY);
                    req.onsuccess = function () {
                        revokedGeneration = (req.result ? req.result.generation : 0) + 1;
                        store.put({
                            key: REVOCATION_KEY,
                            generation: revokedGeneration,
                        });
                    };
                }
                tx.oncomplete = function () {
                    db.close();
                    if (revoke) _observeRevocation({ generation: revokedGeneration });
                    resolve();
                };
                tx.onerror = tx.onabort = function () {
                    db.close();
                    reject(tx.error || new Error("KeyDeleteTransactionAborted"));
                };
            });
        });
    }

    /*
     * ─── Idle wipe (Refs #1065) ─────────────────────────────────────────
     * Couples the key lifetime to the server session: the key must not be
     * usable (and the encrypted Art.-9 bundles must not stay around) once
     * the device has been idle longer than the session age. Re-opening the
     * app offline WITHIN the window keeps working — that is the Streetwork
     * use case the offline mode exists for.
     */

    function _sessionAgeMs() {
        // base.html exposes request.session.get_expiry_age via
        // data-session-age on <body>; login.html / password_change.html
        // load crypto.js without it (and before <body> exists) → fallback.
        // Refs #1966: offline_base.html laesst das Attribut ebenfalls weg —
        // eine precachte Shell darf die Restlaufzeit EINER Sitzung nicht
        // einfrieren. Die precachten Shells laufen damit auf demselben
        // dokumentierten Default wie die Login-Seite.
        const body = document.body;
        const raw = body && body.dataset ? body.dataset.sessionAge : null;
        const parsed = parseInt(raw || "", 10);
        const seconds = Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_SESSION_AGE_S;
        return seconds * 1000;
    }

    function _keyMaxAgeMs() {
        const raw = document.body?.dataset.offlineKeyMaxAge;
        // Neutral offline shells carry no facility policy; the persisted row
        // remains authoritative there. This default is only for derivation.
        const seconds = raw === undefined ? DEFAULT_KEY_MAX_AGE_S : Number(raw);
        if (!Number.isFinite(seconds) || seconds <= 0) {
            throw new Error("InvalidOfflineKeyMaxAge");
        }
        return seconds * 1000;
    }

    function _keyLifetimeValid(row, now) {
        return !!row && Number.isFinite(row.createdAt) && Number.isFinite(row.expiresAt) &&
            Number.isFinite(row.checkedAt) && row.expiresAt > row.createdAt &&
            row.checkedAt >= row.createdAt && now >= row.checkedAt && now < row.expiresAt;
    }

    function _showKeyExpired(expired) {
        document.querySelectorAll("[data-offline-key-expired]").forEach(function (node) {
            node.hidden = !expired;
        });
    }

    // Refs #2528: keep the read and high-water update in one transaction.
    // A delete from any connection is ordered before or after this transaction;
    // no stale row can be inserted again after the delete has completed.
    function _checkKeyLifetime(generation) {
        return _openDb().then(function (db) {
            return new Promise(function (resolve, reject) {
                const tx = db.transaction(META_TABLE, "readwrite");
                const store = tx.objectStore(META_TABLE);
                const req = store.get(KEY_NAME);
                let row = null;
                let expired = false;
                req.onsuccess = function () {
                    if (generation !== keyGeneration) return;
                    row = req.result || null;
                    if (!row || !row.cryptoKey) return;
                    const now = Date.now();
                    expired = !_keyLifetimeValid(row, now);
                    if (!expired) {
                        row.checkedAt = now;
                        store.put(row);
                    }
                };
                tx.oncomplete = function () {
                    db.close();
                    resolve({ row, expired });
                };
                tx.onerror = tx.onabort = function () {
                    db.close();
                    reject(tx.error || new Error("KeyLifetimeTransactionAborted"));
                };
            });
        });
    }

    async function _enforceAbsoluteKeyExpiry() {
        const generation = keyGeneration;
        const { row, expired } = await _checkKeyLifetime(generation);
        // clearSessionKey drops memory immediately, without waiting for the
        // sync lock. A completed check must never undo that local revocation.
        if (generation !== keyGeneration) return false;
        if (!row || !row.cryptoKey) {
            cachedKey = null;
            cachedLifetime = null;
            _showKeyExpired(!!(await _idbGet(EXPIRED_KEY)));
            return false;
        }
        if (expired) {
            // Refs #2528: retain all ciphertext, including unsent work. Keep
            // invalid metadata if deletion fails so the next check retries.
            await clearSessionKey();
            try {
                await _idbPut({ key: EXPIRED_KEY, expired: true });
            } finally {
                _showKeyExpired(true);
                _broadcastKeyCleared();
            }
            return true;
        }
        cachedKey = row.cryptoKey;
        cachedLifetime = row;
        return false;
    }

    function _touchActivity(force) {
        const now = Date.now();
        if (!force && now - lastActivityWrite < ACTIVITY_THROTTLE_MS) {
            return Promise.resolve();
        }
        lastActivityWrite = now;
        return _idbPut({ key: ACTIVITY_KEY, ts: now }).catch(function () {
            // ignore — the next trigger stamps again
        });
    }

    async function wipeOfflineState() {
        // Shared wipe path for logout (sw-register.js) and idle timeout:
        // drop the CryptoKey AND purge the encrypted Dexie store together.
        await clearSessionKey();
        try {
            if (window.offlineStore) {
                await window.offlineStore.purgeAll();
            } else if (document.readyState === "loading") {
                // Boot path: crypto.js executes before offline-store.js —
                // catch up once all synchronous scripts have run.
                document.addEventListener("DOMContentLoaded", function () {
                    if (window.offlineStore) {
                        window.offlineStore.purgeAll().catch(function () {});
                    }
                });
            }
        } catch (_e) {
            // ignore — falls purgeAll() hier scheitert, bleibt das Chiffrat
            // liegen; ohne Schluessel ist es ohnehin unlesbar (F-01). Anders
            // als vor #1352 loest das fehlende purgeAll() ALLEIN keinen
            // Auto-Discard mehr aus (NoSessionKeyError ist TRANSIENT) — der
            // naechste PERMANENTE Decrypt-Fehler (Salt-Rotation/Passwort-
            // wechsel, #576/F-03) oder ein spaeterer purgeExpired()-Lauf mit
            // gueltigem Schluessel raeumt die Reste auf.
        }
    }

    /*
     * Refs #1351/#1383 (M6): Der Idle-Wipe-ENTSCHEID (lastActivity-Read →
     * hasUnsyncedData → clearSessionKey/wipeOfflineState) muss gegen jede
     * laufende Sync-Sequenz serialisiert sein — sonst raeumt der Wipe unter dem
     * einen Tab weg, was ein anderer Tab gerade replayt (TOCTOU #1324 ueber
     * Tabs). Daher unter den origin-weiten Web Lock des Sync-Orchestrators. Ist
     * der Sync-Orchestrator nicht geladen (Login-/Shell-Seiten, alte Engine ohne
     * navigator.locks), laeuft die Kette direkt — heutiges Verhalten.
     *
     * Kein Import-Zyklus: Zugriff nur via ``window.syncOrchestrator?.…``.
     */
    function _runExclusive(fn) {
        if (window.syncOrchestrator && window.syncOrchestrator.runExclusive) {
            return window.syncOrchestrator.runExclusive(fn);
        }
        return (async function () {
            return fn();
        })();
    }

    // Refs #1351/#1383: Nach einem Wipe/Lock den anderen Tabs signalisieren,
    // dass ihr Memory-``cachedKey`` jetzt ohne IndexedDB-Grundlage dasteht —
    // sonst behielten sie ihn bis zu 60s (bis ihr eigener Idle-Interval feuert).
    // Best-effort; BroadcastChannel liefert bewusst nicht an den Absender selbst
    // (kein Echo-Loop).
    function _broadcastKeyCleared() {
        try {
            if (window.syncOrchestrator && window.syncOrchestrator.broadcast) {
                window.syncOrchestrator.broadcast({ type: "key-cleared" });
            }
        } catch (_e) {
            // ignore
        }
    }

    // Refs #1351/#1383: Empfaenger fuer den key-cleared-Broadcast eines ANDEREN
    // Tabs. NUR den Memory-Key verwerfen — die IndexedDB-Loeschung hat der
    // wipende Tab bereits erledigt (KEIN erneuter Wipe), und BroadcastChannel
    // liefert nicht an den Absender zurueck (KEIN Re-Broadcast noetig, kein
    // Echo-Loop). Danach das bestehende UI-Refresh-Signal feuern, damit
    // Banner/Offline-Ansicht den gesperrten Zustand widerspiegeln (der
    // Offline-Viewer rendert dann seinen "bitte neu anmelden"-Zustand).
    //
    // Refs #1393 (Boot-Gate): key-cleared wird NUR konsumiert, wenn der
    // Empfaenger denselben (fehlenden) Key-Zustand teilt. Hat die IndexedDB
    // seit dem Broadcast wieder einen Schluessel (ein Fremd-Tab hat sich neu
    // angemeldet -> deriveSessionKey, unter DEMSELBEN origin-weiten Lock
    // committed), ist der in-memory-Key NICHT verwaist und wird stattdessen
    // re-verankert — der Broadcast eines Boot-Wipes darf keinen frisch
    // abgeleiteten Key wegraeumen. Erst wenn die Grundlage nachweislich fehlt,
    // faellt cachedKey und das UI-Refresh feuert.
    async function _applyKeyCleared() {
        const generation = keyGeneration;
        try {
            await _idbGet(REVOCATION_KEY).then(_observeRevocation);
            const row = await _idbGet(KEY_NAME);
            if (generation !== keyGeneration) return;
            if (row && row.cryptoKey && _keyLifetimeValid(row, Date.now())) {
                // A newly logged-in tab restored the shared key; retain it
                // unless a concurrent local clear invalidated this read.
                cachedKey = row.cryptoKey;
                cachedLifetime = row;
                return;
            }
        } catch (_e) {
            // Lese-Fehler: konservativ verwerfen (Key ohne pruefbare Grundlage).
        }
        cachedKey = null;
        try {
            if (window.offlineEdit && window.offlineEdit.refreshCounts) {
                window.offlineEdit.refreshCounts();
            }
        } catch (_e) {
            // ignore — UI-Refresh ist best-effort
        }
    }

    // Refs #1393 (Boot-Gate): Den Drop unter den origin-weiten Sync-Lock
    // stellen, statt cachedKey sofort aus dem Empfaenger-Kontext zu nullen.
    // Fuehrt DIESER Tab gerade eine Sync-Sequenz (haelt also den Lock), queued
    // die exklusive Anforderung DAHINTER — der laufende Web-Lock-Halter wird
    // nicht von aussen abgebrochen; der (dann per IndexedDB verifizierte) Drop
    // passiert ZWISCHEN Sequenzen. Der in-memory-CryptoKey bleibt bis dahin
    // gueltiges Krypto-Material, sodass die laufende Sequenz sauber zu Ende
    // laeuft (kein transienter No-op-Neuversuch, Refs #1393). Haelt der Tab
    // keinen Lock, laeuft der Drop praktisch sofort. Ohne
    // navigator.locks/Sync-Orchestrator faellt ``_runExclusive`` auf direkte
    // Ausfuehrung zurueck (heutiges Verhalten) — dort feuert mangels
    // BroadcastChannel ohnehin nie ein key-cleared.
    function _onOrchestratorMessage(msg) {
        if (!msg || msg.type !== "key-cleared") return;
        // Invalidate pending derivations even while UI refresh waits for sync.
        keyGeneration += 1;
        _runExclusive(_applyKeyCleared).catch(function () {
            // ignore — best-effort; ein Lese-/Lock-Fehler darf nicht werfen.
        });
    }

    async function enforceIdleWipe() {
        return _runExclusive(_enforceIdleWipeLocked);
    }

    // Every gate checks the absolute key lifetime itself, so a gate entering
    // after the deadline never takes over an earlier gate's result. Only the
    // idle-window decision is shared: one per tab at a time. A gate reached
    // while it is running (as a guest of the lock holder via
    // ``_runIdleGate``, or without any lock where the sync module is missing)
    // joins it. Deciding again would read the stamp before the running wipe
    // deletes it and revoke once more, after a re-login derivation may have
    // started.
    async function _enforceIdleWipeLocked() {
        if (await _enforceAbsoluteKeyExpiry()) return true;
        if (!idleDecision) {
            idleDecision = _decideIdleWindow().finally(function () {
                idleDecision = null;
            });
        }
        return idleDecision;
    }

    async function _decideIdleWindow() {
        const row = await _idbGet(ACTIVITY_KEY);
        if (!row || typeof row.ts !== "number") {
            // No stamp yet (pre-#1065 install or first run): start the idle
            // window now instead of wiping a possibly active session.
            _touchActivity(true);
            return false;
        }
        if (Date.now() - row.ts <= _sessionAgeMs()) {
            return false;
        }
        // Idle-Grenze ueberschritten (Refs #1324): Liegt offline noch
        // ungesyncte Arbeit vor, NUR den Schluessel verwerfen (Lock) statt zu
        // purgen — der verschluesselte Bestand ueberlebt, Re-Login leitet
        // denselben PBKDF2-Schluessel ab und macht ihn wieder lesbar/abspielbar.
        // clearSessionKey allein erfuellt F-01 bereits (Daten ohne Key
        // unlesbar); der Purge ist nur zusaetzliche Defense-in-Depth fuer den
        // sauberen Cache. Im Fehlerfall daher lieber locken als Daten verlieren.
        let unsynced = false;
        try {
            if (window.offlineStore && window.offlineStore.hasUnsyncedData) {
                unsynced = await window.offlineStore.hasUnsyncedData();
            }
        } catch (_e) {
            unsynced = true;
        }
        if (unsynced) {
            await clearSessionKey();
        } else {
            await wipeOfflineState();
        }
        // Refs #1351/#1383: erst NACH dem lokalen Wipe/Lock broadcasten (die
        // Empfaenger verwerfen nur ihren Memory-Key, kein erneuter Wipe/Broadcast).
        _broadcastKeyCleared();
        return true;
    }

    // Refs #1493: true while THIS tab runs a function under the offline lock.
    function _lockHeldByThisTab(orch) {
        const o = orch || window.syncOrchestrator;
        return !!(o && o.isLockHeld && o.isLockHeld());
    }

    // Refs #1493: Reentranz-bewusster Idle-Gate fuer ``_loadKey``.
    //
    // ``_loadKey`` wird auf ZWEI Wegen erreicht:
    //   (1) REENTRANT aus einer bereits gesperrten Sync-Sequenz
    //       (replayQueue/replayModifiedEvent → encrypt/decrypt → _loadKey).
    //       Der Web Lock ist NICHT reentrant — ein erneutes ``_runExclusive``
    //       wuerde sich selbst deadlocken (die haltende Sequenz awaited genau
    //       diesen Aufruf). Daher DIREKT (``_enforceIdleWipeLocked`` ohne Lock)
    //       — schon unter jenem Lock serialisiert.
    //   (2) FREI beim Boot/Decrypt (kein Lock dieses Tabs gehalten). Hier NICHT
    //       mehr ungesperrt (das war das #1493-Restfenster: ein bootender Tab
    //       konnte purgen, waehrend ein Fremd-Tab eine gesperrte Sequenz fuhr),
    //       sondern UNTER dem origin-weiten Lock — er wartet damit auf eine
    //       laufende Fremd-Tab-Sequenz statt mit ihr zu verschraenken.
    //
    // Unterschieden per ``syncOrchestrator.isLockHeld()`` (pro Tab). Der Marker
    // ist ein FALSE-POSITIV-toleranter Konservativ-Schalter: schlaegt er faelsch
    // auf gehalten aus (z.B. Boot waehrend eines EIGENEN Startup-Drains dieses
    // Tabs), degradiert der Gate auf den frueheren ungesperrten Pfad — nie
    // schlechter als heute, und datenverlustfrei (bei ungesyncter Arbeit wird
    // nur der Key verworfen).
    async function _runIdleGate() {
        let orch = window.syncOrchestrator;
        if (!orch) {
            // Seiten-Boot: crypto.js laeuft VOR sync-orchestrator.js. Der erste
            // (eager) ``_loadKey()``-Aufruf entschiede sonst synchron ueber
            // gesperrt-vs-direkt, bevor ``window.syncOrchestrator`` existiert.
            // Ein echter IndexedDB-Roundtrip ist ein Macrotask — wenn er
            // aufloest, ist die synchrone <script>-Kette (inkl.
            // sync-orchestrator.js) gelaufen. (Nur im Boot-Fenster fuellig — ist
            // der Sync-Orchestrator schon geladen, entfaellt der Extra-Read.)
            await _idbGet(ACTIVITY_KEY);
            orch = window.syncOrchestrator;
        }
        if (_lockHeldByThisTab(orch)) {
            return _enforceIdleWipeLocked(); // (1) reentrant → direkt
        }
        // (2) frei → unter dem Lock serialisieren. Ohne Sync-Orchestrator/
        // navigator.locks (Login-/Shell-Seiten, alte Engines) faellt
        // ``_runExclusive`` auf direkte Ausfuehrung zurueck — heutiges Verhalten.
        return _runExclusive(_enforceIdleWipeLocked);
    }

    async function _loadKey() {
        // Refs #2528: check even a cached key before every read/write/replay.
        // The existing reentrancy-aware lock also serializes metadata updates.
        try {
            if (await _runIdleGate()) return null;
        } catch (_e) {
            // Fail closed when the lifetime cannot be checked in IndexedDB.
            cachedKey = null;
            cachedLifetime = null;
            return null;
        }
        return cachedKey;
    }

    // Refs #1493: boot hydration = revocation high-water mark plus key
    // hydration through the idle gate. It settles only once BOTH reads are
    // done, so ``ready()`` never resolves before the idle check. A failed
    // marker read is handled on its own and fails closed: the hydrated key is
    // not exposed, the next ``_loadKey()`` re-checks IndexedDB.
    async function _hydrate() {
        const markerRead = _idbGet(REVOCATION_KEY)
            .then(_observeRevocation)
            .then(
                function () {
                    return true;
                },
                function () {
                    return false;
                }
            );
        const results = await Promise.all([
            markerRead,
            _loadKey().catch(function () {
                return null;
            }),
        ]);
        if (!results[0]) {
            cachedKey = null;
            cachedLifetime = null;
        }
    }

    function _finishInitialLoad() {
        initialLoadSettled = true;
        if (settleInitialLoad) settleInitialLoad();
    }

    // Refs #1493: the page's boot hydration. Under a lock of this tab it is
    // the holder's own run (``hydrateUnderLock``). Without the sync module it
    // runs directly as before; if the sync module shows up while it runs, its
    // idle gate queues under the lock and the holder settles ``initialLoad``
    // with its own run instead of waiting for this one.
    function _runBootHydration() {
        if (_lockHeldByThisTab()) return hydrateUnderLock();
        return _hydrate().then(_finishInitialLoad, _finishInitialLoad);
    }

    // Refs #1493: a lock holder must never wait for something that itself
    // needs the lock. The holder in ``syncOrchestrator.runExclusive`` calls
    // this before it runs its function: while ``initialLoad`` is pending, the
    // holder performs the boot hydration itself -- inside its lock lifetime,
    // awaited completely -- and thereby settles ``initialLoad``. The idle gate
    // takes its reentrant path, serialized by the held lock. Every other chain
    // keeps waiting for ``initialLoad``, which now settles under the lock, so
    // no chain outside the holder gets a direct gate path. The boot request
    // queued behind the holder then finds the hydration done. The holder
    // never joins a hydration started without the lock: that one may wait for
    // a gate queued behind this very holder.
    function hydrateUnderLock() {
        if (initialLoad === null || initialLoadSettled) return Promise.resolve();
        if (!_lockHeldByThisTab()) return initialLoad;
        if (!lockedHydration) {
            lockedHydration = _hydrate().then(_finishInitialLoad, _finishInitialLoad);
        }
        return lockedHydration;
    }

    // Refs #1493: the page's own boot hydration request, under the lock.
    async function _startBootHydration() {
        if (!window.syncOrchestrator) {
            // crypto.js runs before sync-orchestrator.js; one IndexedDB round
            // trip usually lets the synchronous <script> chain run (as in
            // ``_runIdleGate``). If the parser still blocks on a script in
            // between, ``_runBootHydration`` covers the late sync module.
            try {
                await _idbGet(ACTIVITY_KEY);
            } catch (_e) {
                // ignore -- only a scheduling point
            }
        }
        try {
            await _runExclusive(_runBootHydration);
        } catch (_e) {
            // The lock request itself failed. Fail closed unless a holder of
            // this tab is already running the hydration.
            if (!lockedHydration) {
                cachedKey = null;
                cachedLifetime = null;
                _finishInitialLoad();
            }
        }
    }

    // Eager hydration so synchronous hasSessionKey() reflects IndexedDB
    // contents once the page has finished its first tick. _loadKey() also
    // runs the idle gate, so an expired install is wiped on boot (#1065).
    if (typeof window !== "undefined" && "indexedDB" in window) {
        initialLoad = new Promise(function (resolve) {
            settleInitialLoad = resolve;
        });
        _startBootHydration();

        // Activity stamping (throttled) + periodic idle checks (#1065).
        document.addEventListener(
            "pointerdown",
            function () {
                _touchActivity(false);
            },
            { passive: true }
        );
        document.addEventListener(
            "keydown",
            function () {
                _touchActivity(false);
            },
            { passive: true }
        );
        document.addEventListener("visibilitychange", function () {
            if (document.visibilityState === "hidden") {
                // Final stamp when the tab goes away — the idle window for
                // a later (possibly offline) re-open starts here.
                _touchActivity(true);
            } else if (document.visibilityState === "visible") {
                enforceIdleWipe().catch(function () {});
            }
        });
        window.addEventListener("pagehide", function () {
            _touchActivity(true);
        });
        window.setInterval(function () {
            enforceIdleWipe().catch(function () {});
        }, IDLE_CHECK_INTERVAL_MS);

        // Refs #1351/#1383: Cross-Tab-Empfaenger fuer key-cleared beim
        // Sync-Orchestrator registrieren. Der laedt in base.html NACH crypto.js
        // (window-Globals existieren erst spaeter). Refs #1828: das frueher
        // hier duplizierte DOMContentLoaded/naechster-Tick-Retry-Geruest ist
        // durch die Pending-Liste ersetzt, die sync-orchestrator.js beim Laden
        // draint (und danach als Sofort-Registrierung uebernimmt). Ist der
        // Sync-Orchestrator bereits da (Test-Harness, abweichende Reihenfolge),
        // wird direkt registriert. Auf Seiten ohne Sync-Orchestrator (Login/Shell)
        // bleibt der Eintrag folgenlos liegen — das ist der dokumentierte
        // "kein BroadcastChannel → skip"-Fallback.
        if (window.syncOrchestrator && window.syncOrchestrator.onMessage) {
            window.syncOrchestrator.onMessage(_onOrchestratorMessage);
        } else {
            (window.syncOrchestratorPending = window.syncOrchestratorPending || []).push(
                _onOrchestratorMessage
            );
        }
    }

    function ready() {
        return initialLoad || Promise.resolve();
    }

    // Refs #1828: gemeinsames Ready-Gate der Konsumenten. Ersetzt das 15-fach
    // verstreute ``if (window.crypto_session && window.crypto_session.ready) {
    // await window.crypto_session.ready(); }`` — Aufruf-Idiom ist
    // ``await window.crypto_session?.awaitReady?.();`` (tolerant gegenueber
    // Seiten ohne crypto.js und Test-Stubs ohne diese Methode).
    async function awaitReady() {
        await ready();
        await _loadKey();
    }

    async function deriveSessionKey(password, saltB64Url, derivation) {
        const attempt = derivation || beginKeyDerivation();
        const generation = attempt.generation;
        if (!isSupported()) {
            throw new Error("WebCrypto not supported");
        }
        const marker = await attempt.revocation;
        _assertKeyGeneration(generation);
        if (marker.error) {
            cachedKey = null;
            cachedLifetime = null;
            throw marker.error;
        }
        const createdAt = Date.now();
        const expiresAt = createdAt + _keyMaxAgeMs();
        const salt = b64UrlToBytes(saltB64Url);
        const baseKey = await window.crypto.subtle.importKey(
            "raw",
            new TextEncoder().encode(password),
            { name: "PBKDF2" },
            false,
            ["deriveKey"]
        );
        const key = await window.crypto.subtle.deriveKey(
            {
                name: "PBKDF2",
                salt: salt,
                iterations: PBKDF2_ITERATIONS,
                hash: "SHA-256",
            },
            baseKey,
            { name: "AES-GCM", length: KEY_LENGTH },
            false, // non-extractable
            ["encrypt", "decrypt"]
        );
        // Stamp BEFORE storing the key so a stale pre-login stamp can never
        // race the new key into an immediate idle wipe (#1065).
        _assertKeyGeneration(generation);
        await _touchActivity(true);
        const row = { key: KEY_NAME, cryptoKey: key, createdAt, expiresAt, checkedAt: createdAt };
        try {
            await _idbPut(row, generation, marker.value);
        } catch (error) {
            if (error.name === "NoSessionKeyError") {
                cachedKey = null;
                cachedLifetime = null;
            }
            throw error;
        }
        _assertKeyGeneration(generation);
        cachedKey = key;
        cachedLifetime = row;
        await _idbDelete(EXPIRED_KEY);
        _assertKeyGeneration(generation);
        _showKeyExpired(false);
    }

    // Refs #1476 (R7): `aad` bindet den Umschlag an seine Record-Identitaet
    // (Tabelle + Primaerschluessel, von offline-store.js geliefert). Wird ein
    // `aad` uebergeben, traegt der Umschlag `v: ENVELOPE_VERSION` und der
    // Kontext geht als GCM-`additionalData` in den Auth-Tag ein. Ohne `aad`
    // (Alt-Aufrufer / generischer Gebrauch) bleibt das fruehere ungebundene
    // Format erhalten — abwaertskompatibel.
    async function encryptPayload(plain, aad) {
        const key = await _loadKey();
        if (!key) {
            // Refs #1352: typisiert (Praezedenz offline-queue.js) — TRANSIENT,
            // siehe Fehler-Taxonomie im Kopfkommentar.
            const err = new Error("NoSessionKey");
            err.name = "NoSessionKeyError";
            throw err;
        }
        const iv = window.crypto.getRandomValues(new Uint8Array(12));
        const data = new TextEncoder().encode(JSON.stringify(plain));
        const params = { name: "AES-GCM", iv: iv };
        const bound = aad !== undefined && aad !== null && aad !== "";
        if (bound) {
            params.additionalData = _aadBytes(aad);
        }
        const ctBuffer = await window.crypto.subtle.encrypt(params, key, data);
        const envelope = {
            iv: bytesToB64(iv),
            ct: bytesToB64(new Uint8Array(ctBuffer)),
        };
        if (bound) {
            envelope.v = ENVELOPE_VERSION;
        }
        return envelope;
    }

    async function decryptPayload(envelope, aad) {
        const key = await _loadKey();
        if (!key) {
            // Refs #1352: typisiert (Praezedenz offline-queue.js) — TRANSIENT,
            // siehe Fehler-Taxonomie im Kopfkommentar.
            const err = new Error("NoSessionKey");
            err.name = "NoSessionKeyError";
            throw err;
        }
        if (!envelope || !envelope.iv || !envelope.ct) {
            // Refs #1352: typisiert — PERMANENT (kaputter/fremder Datensatz),
            // siehe Fehler-Taxonomie im Kopfkommentar.
            const err = new Error("InvalidEnvelope");
            err.name = "InvalidEnvelopeError";
            throw err;
        }
        const iv = b64ToBytes(envelope.iv);
        const ct = b64ToBytes(envelope.ct);
        const params = { name: "AES-GCM", iv: iv };
        // Refs #1476 (R7): Nur ein v-getragener Umschlag wurde mit AAD
        // geschrieben — dann MUSS mit demselben Kontext entschluesselt werden.
        // Bestands-Umschlaege ohne `v` wurden ohne AAD geschrieben und werden
        // hier auch ohne AAD gelesen (graceful Upgrade), unabhaengig davon, ob
        // die Aufruferin schon ein `aad` mitgibt.
        if (envelope.v) {
            if (aad === undefined || aad === null || aad === "") {
                // Ein v1-Umschlag OHNE Bindungskontext ist ein Programmierfehler
                // an einer neuen Aufrufstelle — NICHT als PERMANENT-Fehler
                // (#576-Auto-Discard = stiller Datenverlust) behandeln, sondern
                // wie TRANSIENT klassifiziert (offline-store.js
                // `_isTransientDecryptError` matcht diesen Namen mit): Row
                // bleibt erhalten, Read liefert null, der Fehler faellt in
                // Tests laut auf.
                const err = new Error("AadRequired");
                err.name = "AadRequiredError";
                throw err;
            }
            params.additionalData = _aadBytes(aad);
        }
        // Refs #1352: bewusst UNGEWRAPPT — ein GCM-Auth-Tag-Mismatch (Salt/
        // Passwort gewechselt ODER umgezogenes/verfaelschtes AAD-gebundenes
        // Chiffrat, R7) wirft nativ eine DOMException mit
        // name === "OperationError" (PERMANENT). Ein try/catch hier wuerde sie
        // in eine generische Error-Instanz verwandeln und die Namens-basierte
        // Transient/Permanent-Unterscheidung in offline-store.js zerstoeren.
        const plainBuffer = await window.crypto.subtle.decrypt(params, key, ct);
        return JSON.parse(new TextDecoder().decode(plainBuffer));
    }

    /** Drop the session key without waiting for the sync lock.
     *
     * Refs #2136, #2528: report persistent deletion without throwing, so
     * logout/quick-exit can still navigate on IndexedDB failure. Memory is
     * cleared immediately and pending writers/hydrators are invalidated.
     * Keep the activity stamp on key deletion failure for the next idle retry.
     */
    async function clearSessionKey() {
        keyGeneration += 1;
        cachedKey = null;
        cachedLifetime = null;
        try {
            await _idbDelete(KEY_NAME, true);
        } catch (_e) {
            // A surviving key still needs the original idle deadline.
            return { persisted: false };
        }
        try {
            // Only a successful key deletion permits a fresh idle window.
            await _idbDelete(ACTIVITY_KEY);
        } catch (_e) {
            // The stamp is not secret and has no effect without the key.
        }
        return { persisted: true };
    }

    /** Liegt (noch) ein persistenter Sitzungsschluessel in der IndexedDB?
     *
     * Refs #2136 (R-L3-13): Rueckleseprobe fuer Aufrufer, die einen
     * Loeschnachweis brauchen statt eines Versprechens. Konservativ im
     * Fehlerfall: Ein Lesefehler liefert ``true`` („koennte da sein"), damit
     * niemand aus einer misslungenen Pruefung eine Zusage ableitet.
     */
    async function hasPersistedSessionKey() {
        try {
            const row = await _idbGet(KEY_NAME);
            return !!(row && row.cryptoKey);
        } catch (_e) {
            return true;
        }
    }

    function hasSessionKey() {
        // Synchronous boolean reflecting the in-memory cache. Callers that
        // need certainty after a fresh page load should `await ready()` first.
        return cachedKey !== null && _keyLifetimeValid(cachedLifetime, Date.now());
    }

    // Refs #1476 (R8): Die Krypto-Primitive (encrypt/decrypt) stehen bewusst
    // NICHT mehr auf der oeffentlichen `window.crypto_session`-Oberflaeche.
    // Zusammen mit dem in IndexedDB persistierten Schluessel waeren sie sonst
    // ein dauerhaft erreichbares Entschluesselungs-Orakel fuer Art.-9-Daten,
    // sobald irgendwo Skript im Origin ausgefuehrt wird (XSS). Stattdessen ein
    // Einmal-Handshake: `_link()` liefert die Primitive beim ERSTEN Aufruf
    // (der einzige legitime Konsument offline-store.js holt sie beim Laden ab)
    // und danach `null`. Ein spaeter injiziertes Skript findet weder
    // decryptPayload auf dem Objekt noch bekommt es sie ueber `_link()`.
    // Primaere Kontrolle bleibt die strikte `script-src 'self'`-CSP; dies ist
    // Defense-in-Depth (Angriffsflaeche verkleinern).
    let _primitives = { encryptPayload: encryptPayload, decryptPayload: decryptPayload };
    function _link() {
        const p = _primitives;
        _primitives = null;
        return p;
    }

    const api = {
        isSupported: isSupported,
        beginKeyDerivation: beginKeyDerivation,
        deriveSessionKey: deriveSessionKey,
        clearSessionKey: clearSessionKey,
        // Refs #2136 (R-L3-13): Rueckleseprobe fuer den Loeschnachweis des
        // Quick-Exit. Bewusst nur LESEND und ohne Schluesselmaterial im
        // Rueckgabewert — die Oberflaechen-Minimierung aus #1476 (R8) bleibt
        // unberuehrt.
        hasPersistedSessionKey: hasPersistedSessionKey,
        wipeOfflineState: wipeOfflineState,
        // Refs #1324: exponiert fuer den deterministischen Idle-Wipe-Trigger
        // (Interval/visibilitychange rufen es intern; Tests treiben es gezielt).
        enforceIdleWipe: enforceIdleWipe,
        hasSessionKey: hasSessionKey,
        ready: ready,
        // Refs #1828: gemeinsames Ready-Gate (siehe awaitReady oben).
        awaitReady: awaitReady,
        // Refs #1493: boot hydration inside a lock holder of this tab.
        hydrateUnderLock: hydrateUnderLock,
        // Refs #1476 (R8): Einmal-Handshake fuer die Krypto-Primitive.
        _link: _link,
    };
    // Refs #1476 (R8): Das oeffentliche Objekt einfrieren (Object.freeze) — so
    // kann ein XSS-Skript keine Methode gegen einen Key-Logger-Shim
    // austauschen und den Logout-/Idle-Wipe (`clearSessionKey`/
    // `wipeOfflineState`) nicht durch Ueberschreiben einer Objekt-Methode
    // deaktivieren. Zusaetzlich non-writable ans window binden, damit ein
    // schlichtes `window.crypto_session = evil` fehlschlaegt. `configurable`
    // bleibt true (der Modul-Reload im JS-Testharness ersetzt die Bindung per
    // defineProperty); die harte Grenze gegen Skriptinjektion ist ohnehin die
    // strikte `script-src 'self'`-CSP.
    Object.defineProperty(window, "crypto_session", {
        value: Object.freeze(api),
        writable: false,
        configurable: true,
        enumerable: true,
    });
})();
