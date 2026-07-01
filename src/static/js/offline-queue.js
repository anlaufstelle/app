/*
 * Offline-Queue: receives QUEUE_REQUEST messages from the Service Worker,
 * stores the request body encrypted at rest in IndexedDB (via offline-store
 * + crypto_session), and replays the queue when the network returns.
 *
 * Refs #573, #576.
 *
 * Failure modes that this module guards against:
 *   - missing CryptoKey (e.g. session timed out): rejects the enqueue so
 *     the user sees an explicit error instead of a silent leak to plaintext
 *     localStorage
 *   - 4xx replay: keeps the record in the queue with `lastError`
 *   - 5xx replay: exponential backoff via `retryAfter`
 *   - multipart/form-data: rejected before enqueue (the SW already returns
 *     503 for this case, this is the defence in depth)
 */
(function () {
    "use strict";

    const MAX_BACKOFF_MS = 30 * 60 * 1000; // 30 min
    const BASE_BACKOFF_MS = 60 * 1000; // 1 min

    function _store() {
        if (!window.offlineStore) {
            throw new Error("OfflineStoreNotLoaded");
        }
        return window.offlineStore;
    }

    function _csrfFromMeta() {
        // Liest den CSRF-Token aus dem <meta name="csrf-token">-Tag, den das
        // Basistemplate rendert. Refs #602: CSRF_COOKIE_HTTPONLY=True verbietet
        // JS-Zugriff auf das Cookie, der Token muss also aus dem DOM kommen.
        if (typeof window.getCsrfToken === "function") {
            return window.getCsrfToken() || null;
        }
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute("content") || null : null;
    }

    function _newIdempotencyKey() {
        // Refs #1109 (F-09): Stabiler Schlüssel pro Queue-Eintrag. Bricht beim
        // Replay die Verbindung nach erfolgreichem Server-Write ab, wird die
        // Zeile erneut gespielt — derselbe Schlüssel lässt den Server den
        // Doppel-Submit erkennen (Header ``X-Idempotency-Key``).
        if (window.crypto && typeof window.crypto.randomUUID === "function") {
            return window.crypto.randomUUID();
        }
        // Fallback für ältere Engines ohne randomUUID: zeit- + zufallsbasiert.
        return "idem-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
    }

    async function _refreshCsrf() {
        try {
            // Fetch rendert beim Zurückkommen aus Offline den Login-Flow neu,
            // inkl. aktualisiertem csrf_token im Meta-Tag (falls die Seite
            // neu geladen wurde) oder zumindest einem frischen Cookie-Paar.
            const resp = await fetch("/login/", {
                method: "GET",
                credentials: "same-origin",
            });
            // Wenn die Login-Seite Text zurückliefert, können wir den Token
            // aus dem HTML parsen (Cookie ist mit HTTPOnly unerreichbar).
            if (resp.ok) {
                const html = await resp.text();
                const m = html.match(/name=["']csrf-token["']\s+content=["']([^"']+)["']/i);
                if (m) return m[1];
            }
        } catch (_e) {
            // network still down
        }
        return _csrfFromMeta();
    }

    async function _updateQueueCount() {
        const count = await _store().count("queue");
        window.dispatchEvent(new CustomEvent("offline-queue-count", { detail: { count: count } }));
    }

    async function enqueueRequest(url, method, body, headers) {
        if (window.crypto_session && window.crypto_session.ready) {
            await window.crypto_session.ready();
        }
        if (!window.crypto_session || !window.crypto_session.hasSessionKey()) {
            const err = new Error("NoSessionKey");
            err.name = "NoSessionKeyError";
            throw err;
        }
        const ct = (headers && headers["content-type"]) || "";
        if (ct.toLowerCase().startsWith("multipart/form-data")) {
            const err = new Error("Multipart uploads cannot be queued offline");
            err.name = "OfflineUploadError";
            throw err;
        }
        await _store().putEncrypted("queue", {
            url: url,
            createdAt: Date.now(),
            attempts: 0,
            retryAfter: 0,
            lastError: "",
            // Refs #1109 (F-09): Idempotenz-Schlüssel einmalig beim Enqueue
            // festlegen, damit er über alle Replay-Versuche stabil bleibt.
            idempotencyKey: _newIdempotencyKey(),
            data: { method: method, body: body, headers: headers || {} },
        });
        await _updateQueueCount();
    }

    async function getQueueCount() {
        try {
            return await _store().count("queue");
        } catch (_e) {
            return 0;
        }
    }

    async function _isReady(record) {
        return !record.retryAfter || record.retryAfter <= Date.now();
    }

    function _backoffFor(attempts) {
        return Math.min(BASE_BACKOFF_MS * Math.pow(2, attempts), MAX_BACKOFF_MS);
    }

    // Baut einen Replay-Request aus einem Queue-Record. Refs #1109 (F-09):
    // Idempotenz-Schlüssel bei jedem Replay-Versuch mitschicken, damit der
    // Server einen Wiederholungs-POST nach Verbindungsabbruch als solchen
    // erkennt.
    function _send(record, csrf) {
        const headers = Object.assign({}, record.data.headers);
        if (csrf) headers["X-CSRFToken"] = csrf;
        if (record.idempotencyKey) headers["X-Idempotency-Key"] = record.idempotencyKey;
        return fetch(record.url, {
            method: record.data.method,
            body: record.data.body,
            headers: headers,
            credentials: "same-origin",
        });
    }

    async function replayQueue() {
        if (!navigator.onLine) return;
        if (window.crypto_session && window.crypto_session.ready) {
            await window.crypto_session.ready();
        }
        if (!window.crypto_session || !window.crypto_session.hasSessionKey()) return;

        const records = await _store().listDecrypted("queue");
        if (records.length === 0) return;

        let csrf = _csrfFromMeta();
        if (!csrf) csrf = await _refreshCsrf();

        for (const record of records) {
            if (!(await _isReady(record))) continue;
            try {
                let response = await _send(record, csrf);
                // Refs #1332: analog #1330 — ein 403 kann an einem zur
                // Precache-Zeit eingefrorenen, veralteten CSRF-Meta (aus einer
                // SW-gecachten Shell) liegen, nicht an fehlendem Recht. Einmal
                // mit frisch von /login/ geholtem Token nachfassen, bevor der
                // Record als 4xx liegen bleibt und die Queue anhaelt. Der
                // frische Token gilt auch fuer die restlichen Records.
                if (response.status === 403) {
                    const fresh = await _refreshCsrf();
                    if (fresh && fresh !== csrf) {
                        csrf = fresh;
                        response = await _send(record, csrf);
                    }
                }
                if (response.ok) {
                    await _store().deleteRow("queue", record.id);
                } else if (response.status === 409) {
                    // Stage 3 (#575) — optimistic concurrency conflict. Do
                    // NOT retry (the stale token would bounce again); mark
                    // the queued record as conflict so the conflict-list
                    // UI can pick it up. The actual merge round-trip goes
                    // through offline-edit.js, but generic queue entries
                    // (e.g. from an offline CREATE-then-EDIT roll-up) may
                    // still hit this branch.
                    await _store().putEncrypted("queue", {
                        ...record,
                        attempts: (record.attempts || 0) + 1,
                        lastError: "409",
                        localStatus: "conflict",
                    });
                    continue; // try the next queued record, don't halt
                } else if (response.status >= 500) {
                    // Server hiccup — exponential backoff, keep record
                    const attempts = (record.attempts || 0) + 1;
                    await _store().putEncrypted("queue", {
                        ...record,
                        attempts: attempts,
                        retryAfter: Date.now() + _backoffFor(attempts),
                        lastError: "" + response.status,
                    });
                    break;
                } else {
                    // 4xx — record stays for user inspection (conflict UI in M6B)
                    await _store().putEncrypted("queue", {
                        ...record,
                        attempts: (record.attempts || 0) + 1,
                        lastError: "" + response.status,
                    });
                    break;
                }
            } catch (_e) {
                // Network gone again; stop and try later
                break;
            }
        }
        await _updateQueueCount();
    }

    window.addEventListener("online", () => {
        replayQueue();
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", _updateQueueCount);
    } else {
        _updateQueueCount();
    }

    window.offlineQueue = {
        enqueueRequest: enqueueRequest,
        replayQueue: replayQueue,
        getQueueCount: getQueueCount,
    };
})();
