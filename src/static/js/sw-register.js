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
        navigator.serviceWorker.register("/sw.js", { scope: "/" }).then(function (registration) {
            // Update-Prompt: Wenn ein neuer SW installiert wird, wird
            // `updatefound` ausgelöst. Der neue SW durchläuft States
            // installing → installed (→ activating → activated). Wenn
            // bereits ein aktiver SW existiert und der neue den State
            // `installed` erreicht, ist ein Update pending.
            //
            // Wir zeigen dem Nutzer einen diskreten Hinweis statt stumm
            // zu aktualisieren (Refs #659). Der User klickt manuell auf
            // "Neu laden" — sonst geht evtl. nicht-gespeicherter State
            // verloren.
            registration.addEventListener("updatefound", function () {
                var newWorker = registration.installing;
                if (!newWorker) return;
                newWorker.addEventListener("statechange", function () {
                    if (newWorker.state === "installed" && navigator.serviceWorker.controller) {
                        _showUpdatePrompt();
                    }
                });
            });
        });

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

    /**
     * Zeigt einen diskreten Hinweis-Toast "Neue Version verfügbar" unten
     * rechts im Viewport. Ein Klick auf "Neu laden" reloaded die Seite —
     * der bereits installierte neue SW übernimmt dann.
     *
     * Kein Alert(), keine Modal-Overlay-Library — nur DOM-Manipulation,
     * kompatibel mit unserer CSP (kein Inline-Script, keine unsafe-*
     * Directives außer den bestehenden für Alpine).
     */
    function _showUpdatePrompt() {
        // Doppel-Trigger verhindern (updatefound kann bei manchen Browsern
        // mehrfach feuern)
        if (document.getElementById("sw-update-toast")) return;

        var toast = document.createElement("div");
        toast.id = "sw-update-toast";
        toast.setAttribute("role", "status");
        toast.setAttribute("aria-live", "polite");
        toast.className =
            "fixed bottom-4 right-4 z-50 max-w-sm bg-indigo-600 text-white " +
            "rounded-lg shadow-lg p-4 flex items-center gap-3";

        var text = document.createElement("span");
        text.className = "text-sm flex-grow";
        text.textContent = "Neue Version verfügbar.";
        toast.appendChild(text);

        var reloadBtn = document.createElement("button");
        reloadBtn.type = "button";
        reloadBtn.className =
            "text-sm font-semibold bg-white text-indigo-700 px-3 py-1 " +
            "rounded hover:bg-indigo-50";
        reloadBtn.textContent = "Neu laden";
        reloadBtn.addEventListener("click", function () {
            window.location.reload();
        });
        toast.appendChild(reloadBtn);

        var dismissBtn = document.createElement("button");
        dismissBtn.type = "button";
        dismissBtn.setAttribute("aria-label", "Schließen");
        dismissBtn.className = "text-white/70 hover:text-white text-lg leading-none";
        dismissBtn.textContent = "×";
        dismissBtn.addEventListener("click", function () {
            toast.remove();
        });
        toast.appendChild(dismissBtn);

        document.body.appendChild(toast);
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
