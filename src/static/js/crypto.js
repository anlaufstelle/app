/*
 * Client-side AES-GCM-256 session key derived from the login password.
 *
 * The CryptoKey is stored as a non-extractable structured-clone object in
 * IndexedDB (table `meta`, key `sessionKey`). Persistence is required to
 * survive page navigations; non-extractability ensures that even an attacker
 * with full IndexedDB read access cannot recover the raw key bytes.
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
 *     fallback 1800 s = SESSION_COOKIE_AGE), key AND offline store are
 *     wiped — on boot/rehydration, on a 60 s interval and on tab return.
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
 * Refs #573, #576, #1065, #1352.
 */
(function () {
    "use strict";

    const PBKDF2_ITERATIONS = 600000;
    const KEY_LENGTH = 256;
    // Separate IndexedDB so the crypto key is not coupled to the Dexie
    // schema version of offline-store.js. Tests that rebuild the offline
    // store would otherwise hit "VersionError" when crypto.js opens v1 of
    // a database that Dexie has already upgraded.
    const DB_NAME = "anlaufstelle-crypto";
    const META_TABLE = "meta";
    const KEY_NAME = "sessionKey";
    // Idle wipe (Refs #1065): lastActivity stamp in the same meta store.
    const ACTIVITY_KEY = "lastActivity";
    const ACTIVITY_THROTTLE_MS = 30 * 1000;
    const IDLE_CHECK_INTERVAL_MS = 60 * 1000;
    const DEFAULT_SESSION_AGE_S = 1800; // fallback = SESSION_COOKIE_AGE

    let cachedKey = null;
    let initialLoad = null; // Promise<void> that resolves once cachedKey reflects IndexedDB
    let lastActivityWrite = 0; // in-memory throttle marker for _touchActivity

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

    function _idbPut(record) {
        return _openDb().then(function (db) {
            return new Promise(function (resolve, reject) {
                const tx = db.transaction(META_TABLE, "readwrite");
                tx.objectStore(META_TABLE).put(record);
                tx.oncomplete = function () {
                    db.close();
                    resolve();
                };
                tx.onerror = function () {
                    db.close();
                    reject(tx.error);
                };
            });
        });
    }

    function _idbDelete(key) {
        return _openDb().then(function (db) {
            return new Promise(function (resolve, reject) {
                const tx = db.transaction(META_TABLE, "readwrite");
                tx.objectStore(META_TABLE).delete(key);
                tx.oncomplete = function () {
                    db.close();
                    resolve();
                };
                tx.onerror = function () {
                    db.close();
                    reject(tx.error);
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
        const body = document.body;
        const raw = body && body.dataset ? body.dataset.sessionAge : null;
        const parsed = parseInt(raw || "", 10);
        const seconds = Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_SESSION_AGE_S;
        return seconds * 1000;
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

    async function enforceIdleWipe() {
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
        return true;
    }

    async function _loadKey() {
        if (cachedKey) return cachedKey;
        // Idle gate (Refs #1065): before rehydrating from IndexedDB, check
        // whether the session age has passed since the last activity — then
        // wipe instead of making the key usable again without re-auth.
        try {
            if (await enforceIdleWipe()) return null;
        } catch (_e) {
            // ignore — a stamp read error must not break online operation
        }
        const row = await _idbGet(KEY_NAME);
        if (row && row.cryptoKey) {
            cachedKey = row.cryptoKey;
        }
        return cachedKey;
    }

    // Eager hydration so synchronous hasSessionKey() reflects IndexedDB
    // contents once the page has finished its first tick. _loadKey() also
    // runs the idle gate, so an expired install is wiped on boot (#1065).
    if (typeof window !== "undefined" && "indexedDB" in window) {
        initialLoad = _loadKey().catch(function () {
            // Ignore — caller will retry via encrypt/decrypt
        });

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
    }

    function ready() {
        return initialLoad || Promise.resolve();
    }

    async function deriveSessionKey(password, saltB64Url) {
        if (!isSupported()) {
            throw new Error("WebCrypto not supported");
        }
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
        await _touchActivity(true);
        cachedKey = key;
        await _idbPut({ key: KEY_NAME, cryptoKey: key });
    }

    async function encryptPayload(plain) {
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
        const ctBuffer = await window.crypto.subtle.encrypt(
            { name: "AES-GCM", iv: iv },
            key,
            data
        );
        return {
            iv: bytesToB64(iv),
            ct: bytesToB64(new Uint8Array(ctBuffer)),
        };
    }

    async function decryptPayload(envelope) {
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
        // Refs #1352: bewusst UNGEWRAPPT — ein GCM-Auth-Tag-Mismatch wirft
        // nativ eine DOMException mit name === "OperationError" (PERMANENT,
        // Salt/Passwort gewechselt). Ein try/catch hier wuerde sie in eine
        // generische Error-Instanz verwandeln und die Namens-basierte
        // Transient/Permanent-Unterscheidung in offline-store.js zerstoeren.
        const plainBuffer = await window.crypto.subtle.decrypt(
            { name: "AES-GCM", iv: iv },
            key,
            ct
        );
        return JSON.parse(new TextDecoder().decode(plainBuffer));
    }

    async function clearSessionKey() {
        cachedKey = null;
        try {
            await _idbDelete(KEY_NAME);
            // Drop the activity stamp with the key: the next idle check
            // starts a fresh window instead of re-wiping forever (#1065).
            await _idbDelete(ACTIVITY_KEY);
        } catch (_e) {
            // ignore — Clear-Site-Data on logout will get rid of it anyway
        }
    }

    function hasSessionKey() {
        // Synchronous boolean reflecting the in-memory cache. Callers that
        // need certainty after a fresh page load should `await ready()` first.
        return cachedKey !== null;
    }

    window.crypto_session = {
        isSupported: isSupported,
        deriveSessionKey: deriveSessionKey,
        encryptPayload: encryptPayload,
        decryptPayload: decryptPayload,
        clearSessionKey: clearSessionKey,
        wipeOfflineState: wipeOfflineState,
        // Refs #1324: exponiert fuer den deterministischen Idle-Wipe-Trigger
        // (Interval/visibilitychange rufen es intern; Tests treiben es gezielt).
        enforceIdleWipe: enforceIdleWipe,
        hasSessionKey: hasSessionKey,
        ready: ready,
    };
})();
