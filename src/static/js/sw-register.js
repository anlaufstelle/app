/*
 * Service Worker registration, offline-queue message bridge, and logout
 * cleanup for the encrypted offline mode.
 *
 * On every logout-form submit (desktop + mobile nav), we:
 *   1. Wipe the in-memory CryptoKey
 *   2. Purge all encrypted IndexedDB stores
 * The server still sends Clear-Site-Data: "storage" as defence in depth.
 *
 * Refs #573, #576.
 */
(function () {
    "use strict";

    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.register("/sw.js", { scope: "/" });

        navigator.serviceWorker.addEventListener("message", async function (event) {
            if (event.data.type === "QUEUE_REQUEST") {
                if (window.offlineQueue) {
                    try {
                        await window.offlineQueue.enqueueRequest(
                            event.data.url,
                            event.data.method,
                            event.data.body,
                            event.data.headers
                        );
                    } catch (e) {
                        // No session key — show a non-intrusive console hint.
                        // The SW already responded with the offline yellow banner,
                        // so the user sees feedback. We just cannot persist.
                        // eslint-disable-next-line no-console
                        console.warn("[offline-queue]", e.message);
                    }
                }
            } else if (event.data.type === "REPLAY_QUEUE") {
                if (window.offlineQueue) await window.offlineQueue.replayQueue();
            }
        });
    }

    function _wipeOfflineState() {
        try {
            if (window.crypto_session) window.crypto_session.clearSessionKey();
            if (window.offlineStore) window.offlineStore.purgeAll();
        } catch (_e) {
            // ignore
        }
    }

    // Hook every logout-form submit. Both desktop and mobile nav use a
    // POST form to {% url 'logout' %} — we match by action attribute.
    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (!form || form.tagName !== "FORM") return;
        var action = form.getAttribute("action") || "";
        if (action.indexOf("/logout/") !== -1 || action.endsWith("/logout/")) {
            _wipeOfflineState();
        }
    });

    // Bootstrap: on every page load with an authenticated user, drop expired
    // records (TTL handling). Salt + key derivation happens in login.html /
    // password_change.html — those templates trigger crypto_session.deriveSessionKey
    // before redirecting away from the login page.
    if (window.offlineStore && document.body.dataset.userId) {
        window.offlineStore.purgeExpired().catch(function () {
            // ignore — better to leave stale records than crash
        });
    }
})();
