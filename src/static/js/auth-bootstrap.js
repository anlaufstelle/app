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
        // SI-2 (#1520/#1499): das personenlose Facility-Meta-Bundle EAGER beim
        // Login-Bootstrap vorwaermen — direkt nach der Schluesselableitung
        // (verschluesseltes At-Rest-Schreiben braucht den Session-Key). So hat
        // die Offline-Create-Shell den Katalog + Feld-Metadaten schon KALT-
        // offline, bevor je eine Person "mitgenommen" wurde.
        // FIRE-AND-FORGET und NICHT-blockierend: ein Fehler (offline beim
        // Login, Ratelimit) darf den Login-Redirect NIE aufhalten;
        // `revalidateCachedFacility` holt ohne gespeicherten ETag voll (200)
        // und der naechste online-Sync revalidiert ohnehin erneut.
        if (window.offlineStore && window.offlineStore.revalidateCachedFacility) {
            window.offlineStore.revalidateCachedFacility().catch(function () {});
        }
        // Refs #867: Server-seitig bestimmter Post-Login-Redirect (z.B.
        // ``/system/`` fuer super_admin). Wird zurueckgegeben, weil der
        // POST /login/-Response mit ``redirect: "manual"`` als
        // opaqueredirect kommt und der Location-Header nicht auslesbar ist.
        return json.home_url || null;
    }

    /*
     * Refs #1415: Zaehlt ungesyncte Offline-Arbeit fuer den Pre-Submit-Guard
     * unten. Kombiniert die zwei vorhandenen Zaehler statt eine eigene
     * Dexie-Abfrage zu duplizieren: ``countUnsyncedEvents`` (Events mit
     * localStatus modified/new/conflict/dead) + das Total aus
     * ``countQueueByStatus`` (generische Replay-Queue) — exakt die Summe,
     * die ``offlineStore.hasUnsyncedData()`` intern als bool prueft
     * (offline-store.js:808-811), hier aber als Zahl fuer die Warnmeldung.
     */
    function unsyncedEntryCount() {
        if (!window.offlineStore) return Promise.resolve(0);
        var events =
            typeof window.offlineStore.countUnsyncedEvents === "function"
                ? window.offlineStore.countUnsyncedEvents()
                : Promise.resolve(0);
        var queue =
            typeof window.offlineStore.countQueueByStatus === "function"
                ? window.offlineStore.countQueueByStatus()
                : Promise.resolve({ total: 0 });
        return Promise.all([events, queue]).then(function (results) {
            return (results[0] || 0) + ((results[1] && results[1].total) || 0);
        });
    }

    /*
     * Refs #1415: Pre-Submit-Guard fuer den Passwortwechsel. Ein
     * Passwortwechsel rotiert das Offline-Salt und macht bestehende
     * Offline-Chiffrate kryptografisch unlesbar (docs/user-guide.md §8) —
     * das ist bereits mit dem POST besiegelt, ein `confirm` DANACH koennte
     * nichts mehr retten. Der Guard warnt daher ausschliesslich pre-submit.
     *
     * FAIL-OPEN (Pflicht): schlaegt der Zaehl-Check fehl, fehlt
     * window.offlineStore oder ist der Offline-Modus nicht initialisiert,
     * geht der Passwortwechsel ungehindert durch — Passwortwechsel ist
     * sicherheitskritisch und darf nie blockiert werden. `confirm`
     * erscheint nur, wenn der Zaehler nachweislich > 0 ist.
     *
     * Async/sync-Bruch (`confirm()` ist synchron, der Zaehl-Check async):
     * `preventDefault` + `stopImmediatePropagation` auf dem ersten Submit,
     * dann nach dem (asynchronen) Check ein programmatischer
     * `form.requestSubmit()`. Ein `bypass`-Flag laesst den zweiten,
     * programmatischen Submit unangetastet durch — kein natives
     * `form.submit()` (das wuerde den unten registrierten Fetch-basierten
     * Handler von `attach()` umgehen und die Salt-Ableitung nach dem
     * Passwortwechsel verpassen) und keine Rekursion/Endlosschleife, weil
     * der Guard-Listener beim Bypass fruehzeitig zurueckkehrt.
     */
    function attachUnsyncedGuard(form) {
        var bypass = false;
        form.addEventListener("submit", function (event) {
            if (bypass) return; // programmatischer Re-Submit nach confirm/Fail-Open
            event.preventDefault();
            event.stopImmediatePropagation();
            unsyncedEntryCount()
                .then(function (count) {
                    if (count > 0) {
                        var template = form.getAttribute("data-unsynced-confirm-text") || "";
                        var msg = template.replace("{count}", String(count));
                        if (msg && !window.confirm(msg)) return; // Abbruch: erst synchronisieren
                    }
                    bypass = true;
                    if (form.requestSubmit) form.requestSubmit();
                    else form.submit();
                })
                .catch(function () {
                    // FAIL-OPEN: Zaehl-Check fehlgeschlagen → nicht blockieren.
                    bypass = true;
                    if (form.requestSubmit) form.requestSubmit();
                    else form.submit();
                });
        });
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
    var passwordChangeForm = document.getElementById("password-change-form");
    if (passwordChangeForm) {
        // Muss VOR attach() registriert werden: addEventListener-Reihenfolge
        // entscheidet, wer beim ersten Submit zuerst laeuft — der Guard
        // braucht stopImmediatePropagation, um den Fetch-Handler von
        // attach() beim (asynchronen) Zaehl-Check anzuhalten.
        attachUnsyncedGuard(passwordChangeForm);
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
