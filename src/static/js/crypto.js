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
 *   - The browser's Clear-Site-Data: "storage" header on logout drops the
 *     entire IndexedDB as defence in depth.
 *
 * Refs #573, #576.
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

    let cachedKey = null;
    let initialLoad = null; // Promise<void> that resolves once cachedKey reflects IndexedDB

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

    async function _loadKey() {
        if (cachedKey) return cachedKey;
        const row = await _idbGet(KEY_NAME);
        if (row && row.cryptoKey) {
            cachedKey = row.cryptoKey;
        }
        return cachedKey;
    }

    // Eager hydration so synchronous hasSessionKey() reflects IndexedDB
    // contents once the page has finished its first tick.
    if (typeof window !== "undefined" && "indexedDB" in window) {
        initialLoad = _loadKey().catch(function () {
            // Ignore — caller will retry via encrypt/decrypt
        });
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
        hasSessionKey: hasSessionKey,
        ready: ready,
    };
})();
