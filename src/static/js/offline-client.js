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

    // Refs #1408: die gemeinsame CSRF-Logik lebt in csrf-utils.js
    // (window.csrfUtils). Zur CALL-Zeit aufloesen und tolerant bleiben, falls
    // das Util wider Erwarten fehlt (kein Crash) — dann null wie beim leeren Meta.
    function _csrfFromMeta() {
        return window.csrfUtils ? window.csrfUtils.fromMeta() : null;
    }

    function _bundleUrl(clientPk) {
        return "/api/v1/offline/bundle/client/" + encodeURIComponent(clientPk) + "/";
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

        // Refs #1356: Einmalige Anfrage um dauerhaften Speicher (Eviction-
        // Schutz). Ergebnis wird von offlineStore selbst gecacht; eine
        // Verweigerung oder ein Fehler hier darf die Mitnahme NIE blockieren.
        let persisted = null;
        try {
            persisted = await window.offlineStore.ensurePersistentStorage();
        } catch (_e) {
            // Geschluckt — siehe Kommentar oben.
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
        // Refs #1410 (a): Content-ETag der Erst-Mitnahme mitspeichern, damit die
        // spaetere periodische Revalidierung bedingt (If-None-Match) fragen und
        // bei unveraendertem Bundle einen 304 (statt vollem Re-Download) bekommen
        // kann.
        const etag = response.headers.get("ETag");
        const saveResult = await store.saveClientBundle(bundle, etag);
        await _emitCountEvent();
        // Refs #1356: Aufrufer (Badge/Listen-Toggle) haengen bei Verweigerung
        // einen dezenten Hinweis an ihre Erfolgsmeldung. `null` (API fehlt/
        // Fehler) zaehlt NICHT als Verweigerung.
        bundle.persistDenied = persisted === false;
        // Refs #1351/#1385 (M8/Task 4): Re-Take-Rueckmeldung — wie viele
        // ungesyncte Aenderungen dieses (Re-)Takes ueberlebt haben (Aufrufer
        // zeigt ggf. "<N> lokale Aenderungen beibehalten").
        bundle.survivingEdits = (saveResult && saveResult.survivingEdits) || 0;
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
