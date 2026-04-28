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

    function _csrfFromCookie() {
        const match = document.cookie.match(/csrftoken=([^;]+)/);
        return match ? match[1] : null;
    }

    async function _refreshCsrf() {
        try {
            await fetch("/login/", { method: "GET", credentials: "same-origin" });
        } catch (_e) {
            // network still down
        }
        return _csrfFromCookie();
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

    async function replayQueue() {
        if (!navigator.onLine) return;
        if (window.crypto_session && window.crypto_session.ready) {
            await window.crypto_session.ready();
        }
        if (!window.crypto_session || !window.crypto_session.hasSessionKey()) return;

        const records = await _store().listDecrypted("queue");
        if (records.length === 0) return;

        let csrf = _csrfFromCookie();
        if (!csrf) csrf = await _refreshCsrf();

        for (const record of records) {
            if (!(await _isReady(record))) continue;
            const headers = Object.assign({}, record.data.headers);
            if (csrf) headers["X-CSRFToken"] = csrf;
            try {
                const response = await fetch(record.url, {
                    method: record.data.method,
                    body: record.data.body,
                    headers: headers,
                    credentials: "same-origin",
                });
                if (response.ok) {
                    await _store().deleteRow("queue", record.id);
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
