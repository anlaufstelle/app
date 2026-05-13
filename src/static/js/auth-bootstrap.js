/*
 * Login + password-change bootstrap for the encrypted offline mode.
 *
 * - On the login page: intercept the submit, fetch /login/, then
 *   POST /auth/offline-key-salt/ and call crypto_session.deriveSessionKey()
 *   with the user's password before navigating away. Falls back to a native
 *   form submit if WebCrypto is unavailable.
 * - On the password-change page: same flow with the new password, after
 *   purging any stale ciphertext.
 *
 * Lives in its own file (not inline) because the project's CSP forbids
 * inline scripts. Refs #573, #576.
 */
(function () {
    "use strict";

    if (!window.crypto_session || !window.crypto_session.isSupported()) return;

    function csrfFromMeta() {
        // Refs #602: CSRF_COOKIE_HTTPONLY schließt JS aus dem Cookie aus, der
        // Token kommt aus dem <meta name="csrf-token">-Tag im Login-Template.
        if (typeof window.getCsrfToken === "function") {
            return window.getCsrfToken() || null;
        }
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute("content") || null : null;
    }

    async function fetchFreshCsrfToken(location) {
        // Refs #602/#613: CSRF_COOKIE_HTTPONLY blocks JS from reading the rotated
        // post-login cookie, und das Meta-Tag auf der Login-Page haelt nur
        // den Pre-Login-Token. Das Redirect-Ziel hat den frischen Token,
        // ist aber rollenabhaengig (super_admin sieht z.B. ``/`` nicht).
        // Refs #867: faellt auf ``/login/`` zurueck — diese Seite ist fuer
        // jede authentifizierte Rolle erreichbar (200 OK) und enthaelt das
        // ``<meta name="csrf-token">`` mit dem aktuellen (post-login) Token.
        async function tryFetch(url) {
            try {
                var resp = await fetch(url, { method: "GET", credentials: "same-origin" });
                if (!resp.ok) return null;
                var html = await resp.text();
                var match = html.match(
                    /<meta[^>]+name=["']csrf-token["'][^>]+content=["']([^"']+)["']/i
                );
                return match ? match[1] : null;
            } catch (e) {
                return null;
            }
        }
        var token = await tryFetch(location);
        if (token) return token;
        // Fallback: /login/ ist immer erreichbar (auch fuer super_admin
        // ohne Facility-Kontext) und liefert nach Login den rotierten Token.
        return await tryFetch("/login/");
    }

    async function fetchSaltAndDeriveKey(password, location) {
        var token = (await fetchFreshCsrfToken(location)) || csrfFromMeta() || "";
        var saltResp = await fetch("/auth/offline-key-salt/", {
            method: "POST",
            credentials: "same-origin",
            headers: { "X-CSRFToken": token },
        });
        if (!saltResp.ok) return null;
        var json = await saltResp.json();
        await window.crypto_session.deriveSessionKey(password, json.salt);
        // Refs #867: Server-seitig bestimmter Post-Login-Redirect (z.B.
        // ``/system/`` fuer super_admin). Wird zurueckgegeben, weil der
        // POST /login/-Response mit ``redirect: "manual"`` als
        // opaqueredirect kommt und der Location-Header nicht auslesbar ist.
        return json.home_url || null;
    }

    function attach(formId, passwordField, before) {
        var form = document.getElementById(formId);
        if (!form) return;
        var disarmed = false;
        form.addEventListener("submit", function (event) {
            if (disarmed) return; // native fallback path
            event.preventDefault();
            var formData = new FormData(form);
            var password = formData.get(passwordField);
            fetch(form.action || window.location.pathname, {
                method: "POST",
                body: formData,
                redirect: "manual",
                credentials: "same-origin",
            })
                .then(function (resp) {
                    var redirected =
                        resp.status === 302 ||
                        resp.status === 303 ||
                        resp.type === "opaqueredirect" ||
                        resp.status === 0;
                    if (!redirected) {
                        // Server-rendered errors → fall back to native submit
                        disarmed = true;
                        form.submit();
                        return null;
                    }
                    // Refs #867: Location-Header ist bei
                    // ``redirect: "manual"`` nicht auslesbar (opaqueredirect).
                    // Server-seitige Bestimmung kommt zurueck via salt-Endpoint
                    // unten; "/" ist der Fallback fuer alle Nicht-super_admin-User
                    // (entspricht LOGIN_REDIRECT_URL).
                    var fallbackLocation = "/";
                    var beforePromise = before ? before() : Promise.resolve();
                    return beforePromise
                        .then(function () {
                            return fetchSaltAndDeriveKey(password, fallbackLocation);
                        })
                        .then(function (homeUrl) {
                            return homeUrl || fallbackLocation;
                        });
                })
                .then(function (location) {
                    if (location) window.location.href = location;
                })
                .catch(function () {
                    disarmed = true;
                    form.submit();
                });
        });
    }

    if (document.getElementById("login-form")) {
        attach("login-form", "password");
    }
    if (document.getElementById("password-change-form")) {
        attach("password-change-form", "new_password1", function () {
            // Purge any stale ciphertext that was encrypted with the old key
            var p1 = window.crypto_session
                ? window.crypto_session.clearSessionKey()
                : Promise.resolve();
            var p2 = window.offlineStore
                ? window.offlineStore.purgeAll().catch(function () {})
                : Promise.resolve();
            return Promise.all([p1, p2]);
        });
    }
})();
