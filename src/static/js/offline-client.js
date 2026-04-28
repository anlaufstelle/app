/*
 * Offline-Client Helper (Stage 2, Refs #574, #572).
 *
 * Thin orchestration layer on top of offline-store.js:
 *   - takeClientOffline(pk): fetch server bundle, save it encrypted.
 *   - removeClientFromOffline(pk): drop all locally cached records.
 *   - isClientOffline(pk): boolean (for UI badges).
 *   - getLocallyCachedClients(): list of {pk, lastSynced} for the banner.
 *
 * The module never persists plaintext — saveClientBundle handles encryption
 * through the session key. If the browser has no session key (e.g. after a
 * Clear-Site-Data logout), takeClientOffline throws so the UI can tell the
 * user to re-authenticate instead of silently failing.
 */
(function () {
    "use strict";

    const MAX_OFFLINE_CLIENTS = 20;

    function _store() {
        if (!window.offlineStore) {
            throw new Error("OfflineStoreNotLoaded");
        }
        return window.offlineStore;
    }

    function _csrfFromMeta() {
        // Refs #602: CSRF_COOKIE_HTTPONLY verbietet JS-Zugriff aufs Cookie,
        // Token kommt aus dem <meta name="csrf-token">-Tag im Basistemplate.
        if (typeof window.getCsrfToken === "function") {
            return window.getCsrfToken() || null;
        }
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute("content") || null : null;
    }

    function _bundleUrl(clientPk) {
        return "/api/offline/bundle/client/" + encodeURIComponent(clientPk) + "/";
    }

    async function _emitCountEvent() {
        try {
            const count = await _store().countOfflineClients();
            window.dispatchEvent(
                new CustomEvent("offline-clients-count", { detail: { count: count } })
            );
        } catch (_e) {
            // Not fatal.
        }
    }

    async function takeClientOffline(clientPk) {
        if (!window.crypto_session) {
            throw new Error("CryptoSessionNotLoaded");
        }
        if (window.crypto_session.ready) {
            await window.crypto_session.ready();
        }
        if (!window.crypto_session.hasSessionKey()) {
            const err = new Error("NoSessionKey");
            err.name = "NoSessionKeyError";
            throw err;
        }

        const store = _store();
        const currentCount = await store.countOfflineClients();
        const already = await store.isClientOffline(clientPk);
        if (!already && currentCount >= MAX_OFFLINE_CLIENTS) {
            const err = new Error(
                "Offline-Cache-Limit erreicht (" + MAX_OFFLINE_CLIENTS + " Klientel)."
            );
            err.name = "OfflineLimitError";
            throw err;
        }

        const csrf = _csrfFromMeta();
        const headers = { Accept: "application/json" };
        if (csrf) headers["X-CSRFToken"] = csrf;

        const response = await fetch(_bundleUrl(clientPk), {
            method: "GET",
            credentials: "same-origin",
            headers: headers,
        });
        if (!response.ok) {
            const err = new Error("BundleFetchFailed:" + response.status);
            err.name = "BundleFetchError";
            err.status = response.status;
            throw err;
        }
        const bundle = await response.json();
        await store.saveClientBundle(bundle);
        await _emitCountEvent();
        return bundle;
    }

    async function removeClientFromOffline(clientPk) {
        await _store().removeOfflineClient(clientPk);
        await _emitCountEvent();
    }

    async function isClientOffline(clientPk) {
        try {
            return await _store().isClientOffline(clientPk);
        } catch (_e) {
            return false;
        }
    }

    async function getOfflineClient(clientPk) {
        return _store().getOfflineClient(clientPk);
    }

    async function getLocallyCachedClients() {
        try {
            return await _store().listOfflineClients();
        } catch (_e) {
            return [];
        }
    }

    async function refreshCountBadge() {
        await _emitCountEvent();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", refreshCountBadge);
    } else {
        refreshCountBadge();
    }

    window.offlineClient = {
        takeClientOffline: takeClientOffline,
        removeClientFromOffline: removeClientFromOffline,
        isClientOffline: isClientOffline,
        getOfflineClient: getOfflineClient,
        getLocallyCachedClients: getLocallyCachedClients,
        refreshCountBadge: refreshCountBadge,
        MAX_OFFLINE_CLIENTS: MAX_OFFLINE_CLIENTS,
    };
})();
