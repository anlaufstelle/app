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
 * Refs #573, #576, #1065.
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
            // ignore — leftover ciphertext is useless without the key and
            // decryptPayload failures auto-discard rows anyway (#576).
        }
    }

    async function _enforceIdleWipe() {
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
        await wipeOfflineState();
        return true;
    }

    async function _loadKey() {
        if (cachedKey) return cachedKey;
        // Idle gate (Refs #1065): before rehydrating from IndexedDB, check
        // whether the session age has passed since the last activity — then
        // wipe instead of making the key usable again without re-auth.
        try {
            if (await _enforceIdleWipe()) return null;
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
                _enforceIdleWipe().catch(function () {});
            }
        });
        window.addEventListener("pagehide", function () {
            _touchActivity(true);
        });
        window.setInterval(function () {
            _enforceIdleWipe().catch(function () {});
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
        if (!key) throw new Error("NoSessionKey");
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
        if (!key) throw new Error("NoSessionKey");
        if (!envelope || !envelope.iv || !envelope.ct) {
            throw new Error("InvalidEnvelope");
        }
        const iv = b64ToBytes(envelope.iv);
        const ct = b64ToBytes(envelope.ct);
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
        hasSessionKey: hasSessionKey,
        ready: ready,
    };
})();
