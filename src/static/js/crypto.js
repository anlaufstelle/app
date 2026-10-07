/*
 * Client-side AES-GCM-256 session key derived from the login password.
 *
 * The CryptoKey is stored as a non-extractable structured-clone object in
 * IndexedDB (table `meta`, key `sessionKey`). Persistence is required to
 * survive page navigations, and the key also outlives closing the tab, the
 * browser or a device restart. Non-extractable only means the raw key bytes
 * cannot be read out; until the row is deleted, any code running in this
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
        try {
            const row = await _idbGet(KEY_NAME);
            if (row && row.cryptoKey) {
                // Geteilter Key-Zustand NICHT (mehr) fehlend -> re-verankern,
                // kein Drop, kein Sequenz-Abbruch.
                cachedKey = row.cryptoKey;
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
        _runExclusive(_applyKeyCleared).catch(function () {
            // ignore — best-effort; ein Lese-/Lock-Fehler darf nicht werfen.
        });
    }

    async function enforceIdleWipe() {
        return _runExclusive(_enforceIdleWipeLocked);
    }

    async function _enforceIdleWipeLocked() {
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
        if (orch && orch.isLockHeld && orch.isLockHeld()) {
            return _enforceIdleWipeLocked(); // (1) reentrant → direkt
        }
        // (2) frei → unter dem Lock serialisieren. Ohne Sync-Orchestrator/
        // navigator.locks (Login-/Shell-Seiten, alte Engines) faellt
        // ``_runExclusive`` auf direkte Ausfuehrung zurueck — heutiges Verhalten.
        return _runExclusive(_enforceIdleWipeLocked);
    }

    async function _loadKey() {
        if (cachedKey) return cachedKey;
        // Idle gate (Refs #1065): before rehydrating from IndexedDB, check
        // whether the session age has passed since the last activity — then
        // wipe instead of making the key usable again without re-auth. Der Gate
        // laeuft reentranz-bewusst gesperrt/direkt (Refs #1493, siehe
        // ``_runIdleGate``).
        try {
            if (await _runIdleGate()) return null;
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

    /** Sitzungsschluessel verwerfen.
     *
     * Refs #2136 (R-L3-13): Die Funktion **wirft weiterhin nicht** — jeder
     * bestehende Aufrufer (``wipeOfflineState``, ``_enforceIdleWipeLocked``,
     * der Passwortwechsel in auth-bootstrap.js, die Offline-E2E) ``await``et
     * sie und wuerde von einer Rejection unvermittelt abbrechen; beim Logout
     * waere ein abgebrochener Wipe schlimmer als ein stiller. Stattdessen
     * MELDET sie den Ausgang: ``{persisted: true}`` heisst „die persistente
     * Zeile ist nachweislich weg", ``{persisted: false}`` heisst „der
     * IndexedDB-Delete ist gescheitert, die Zeile kann noch da sein".
     *
     * Vorher verschluckte der ``catch`` beide Delete-Fehler und erfuellte das
     * Promise trotzdem — ein Aufrufer konnte einen Fehlschlag nicht von einem
     * Erfolg unterscheiden. Fuer den Normalfall (Logout mit
     * ``Clear-Site-Data``) war das folgenlos; fuer „Schnell verlassen" ist es
     * das nicht: dort ist die Loeschung offline der einzige Riegel, und ein
     * faelschlich als Erfolg gewerteter Fehlschlag loeste einen
     * ``key-cleared``-Broadcast aus, der in einem Fremd-Tab den Schluessel
     * ueber ``_applyKeyCleared`` sogar wieder verankert haette.
     *
     * Der Memory-Key faellt in JEDEM Fall — daran aendert sich nichts. Der
     * Aktivitaetsstempel dagegen faellt NUR mit einem nachweislich geloeschten
     * Schluessel (R-L3-14): Er ist der Ausloeser des naechsten Idle-Laufs, und
     * ein ueberlebender Schluessel braucht genau den.
     */
    async function clearSessionKey() {
        cachedKey = null;
        try {
            await _idbDelete(KEY_NAME);
        } catch (_e) {
            // R-L3-14: Der Aktivitaetsstempel bleibt hier BEWUSST stehen.
            // Scheitert der Delete der Schluesselzeile, liegt der Schluessel
            // weiterhin auf dem Geraet — und dann ist der alte, laengst
            // ueberfaellige Stempel genau das, was den naechsten Idle-Lauf
            // erneut ausloest. Wuerde er mitgeloescht, faende
            // ``_enforceIdleWipeLocked`` kein ``ts`` mehr, startete ein FRISCHES
            // Zeitfenster (siehe dort) und verschoebe die naechste
            // Loeschprobe um eine volle Sitzungsdauer — ausgerechnet in dem
            // Fall, in dem sie noetig ist.
            return { persisted: false };
        }
        try {
            // Drop the activity stamp with the key: the next idle check
            // starts a fresh window instead of re-wiping forever (#1065).
            // Erst HIER, wo der Schluessel nachweislich weg ist — dann gibt es
            // auch nichts mehr, was ein erneuter Wipe holen muesste.
            await _idbDelete(ACTIVITY_KEY);
        } catch (_e) {
            // Der Stempel ist kein Geheimnis und ohne Key wirkungslos — sein
            // Fehlschlag entwertet die Schluessel-Loeschung nicht.
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
        return cachedKey !== null;
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
