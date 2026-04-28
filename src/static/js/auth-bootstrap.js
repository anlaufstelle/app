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

    function csrfFromCookie() {
        var m = document.cookie.match(/csrftoken=([^;]+)/);
        return m ? m[1] : null;
    }

    async function fetchSaltAndDeriveKey(password) {
        var saltResp = await fetch("/auth/offline-key-salt/", {
            method: "POST",
            credentials: "same-origin",
            headers: { "X-CSRFToken": csrfFromCookie() || "" },
        });
        if (!saltResp.ok) return;
        var json = await saltResp.json();
        await window.crypto_session.deriveSessionKey(password, json.salt);
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
                    var location = resp.headers.get("Location") || "/";
                    var beforePromise = before ? before() : Promise.resolve();
                    return beforePromise
                        .then(function () {
                            return fetchSaltAndDeriveKey(password);
                        })
                        .then(function () {
                            return location;
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
