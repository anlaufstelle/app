/*
 * Offline-Edit Orchestrator (Stage 3, Refs #575, #572).
 *
 * Bridges the offline-read-cache (Stage 2) and the generic offline-queue
 * (Stage 1): when the user edits an event while offline, this module writes
 * the edited state into IndexedDB under `localStatus: "modified"` plus the
 * `expected_updated_at` token from the cached copy. When the network returns
 * we replay the edit against `/events/<pk>/edit/` with an
 * `Accept: application/json` header; the server honours that with either a
 * redirect (success) or a 409 JSON body (conflict) that we stash back into
 * IndexedDB under `localStatus: "conflict"` for the merge UI to pick up.
 *
 * This module deliberately does NOT use the generic queue — offline edits
 * must round-trip through a dedicated replay because they need to read the
 * 409 response body and carry per-event status information, both of which
 * the queue's fire-and-forget model does not provide.
 */
(function () {
    "use strict";

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
            // Still offline, ignore.
        }
        return _csrfFromCookie();
    }

    function _eventEditUrl(eventPk) {
        return "/events/" + encodeURIComponent(eventPk) + "/edit/";
    }

    function _fireCountEvent() {
        // Let the Alpine banner in base.html react without polling.
        _store()
            .countUnsyncedEvents()
            .then((count) => {
                window.dispatchEvent(
                    new CustomEvent("offline-unsynced-count", { detail: { count: count } })
                );
            })
            .catch(() => {
                /* ignore — banner just stays at its last known count */
            });
        _store()
            .countConflictEvents()
            .then((count) => {
                window.dispatchEvent(
                    new CustomEvent("offline-conflict-count", { detail: { count: count } })
                );
            })
            .catch(() => {});
    }

    /*
     * Persist a modified event in IndexedDB.
     *
     * `formData` is the flat object of slug → value that the edit form
     * produces (plus `expected_updated_at` either from the cached record
     * or from a prior conflict resolution). The record lands in the
     * `events` table where `getOfflineClient` already reads from, so the
     * read-cache immediately reflects the pending change.
     */
    async function markEventModified(eventPk, formData, options) {
        const opts = options || {};
        const record = {
            pk: String(eventPk),
            clientPk: opts.clientPk || "",
            occurredAt: opts.occurredAt || "",
            localStatus: opts.localStatus || "modified",
            data: {
                formData: formData,
                expectedUpdatedAt: opts.expectedUpdatedAt || "",
                // Snapshot a few metadata fields so the list/review UI can
                // label the edit without cross-referencing the cache.
                documentTypeName: opts.documentTypeName || "",
                lastEditedAt: Date.now(),
            },
        };
        await _store().saveOfflineEdit(record);
        _fireCountEvent();
        return record;
    }

    async function replayModifiedEvent(record) {
        if (!navigator.onLine) return { status: "offline" };
        if (window.crypto_session && window.crypto_session.ready) {
            await window.crypto_session.ready();
        }
        if (!window.crypto_session || !window.crypto_session.hasSessionKey()) {
            return { status: "no-key" };
        }

        let csrf = _csrfFromCookie();
        if (!csrf) csrf = await _refreshCsrf();

        const form = new URLSearchParams();
        const fd = (record.data && record.data.formData) || {};
        for (const [key, value] of Object.entries(fd)) {
            if (value === null || value === undefined) continue;
            form.append(key, String(value));
        }
        if (record.data && record.data.expectedUpdatedAt) {
            form.set("expected_updated_at", record.data.expectedUpdatedAt);
        }

        const headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            Accept: "application/json",
        };
        if (csrf) headers["X-CSRFToken"] = csrf;

        let response;
        try {
            response = await fetch(_eventEditUrl(record.pk), {
                method: "POST",
                body: form.toString(),
                headers: headers,
                credentials: "same-origin",
            });
        } catch (_e) {
            return { status: "network-error" };
        }

        if (response.ok || response.status === 302) {
            // Server accepted the edit. Drop the local record so future
            // replays don't re-submit and force a re-sync next time the
            // client opens the event.
            await _store().clearOfflineEdit(record.pk);
            _fireCountEvent();
            return { status: "synced" };
        }
        if (response.status === 409) {
            let body = null;
            try {
                body = await response.json();
            } catch (_e) {
                body = { error: "conflict", server_state: {} };
            }
            await enqueueConflictForReview(record, body.server_state || {});
            return { status: "conflict", serverState: body.server_state };
        }
        // Any other 4xx/5xx — keep the record untouched so the user can
        // inspect in the modified-list; mark attempts so the UI can show
        // a retry hint.
        return { status: "error", statusCode: response.status };
    }

    async function enqueueConflictForReview(record, serverState) {
        await _store().saveConflictState(record.pk, serverState || {});
        _fireCountEvent();
    }

    async function replayAllModifiedEvents() {
        // Best-effort loop: stop on the first network error so we don't
        // spam the server with failing requests, but carry on past a
        // conflict — conflicts are a per-record state, not a session-wide
        // halt.
        const records = await _store().listModifiedEvents();
        for (const record of records) {
            const result = await replayModifiedEvent(record);
            if (result.status === "network-error" || result.status === "offline") {
                break;
            }
        }
    }

    // When the browser comes back online, drain the offline-edit queue
    // alongside the generic offline-queue. Independent from
    // offline-queue.js so that an edit never gets silently retried by the
    // generic queue (which cannot handle the 409 body).
    window.addEventListener("online", () => {
        replayAllModifiedEvents();
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", _fireCountEvent);
    } else {
        _fireCountEvent();
    }

    window.offlineEdit = {
        markEventModified: markEventModified,
        replayModifiedEvent: replayModifiedEvent,
        replayAllModifiedEvents: replayAllModifiedEvents,
        enqueueConflictForReview: enqueueConflictForReview,
        refreshCounts: _fireCountEvent,
    };
})();
