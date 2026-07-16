/*
 * Service Worker registration, offline-queue message bridge, and logout
 * cleanup for the encrypted offline mode.
 *
 * On every logout-form submit (desktop + mobile nav), we:
 *   1. Wipe the in-memory CryptoKey
 *   2. Purge all encrypted IndexedDB stores
 * The server still sends Clear-Site-Data: "storage" as defence in depth.
 *
 * Refs #573, #576, #1351, #1386, #1593.
 */
(function () {
    "use strict";

    // Refs #1593: Aktivierung und Reload sind ENTKOPPELT.
    //
    // Aktivierung (SKIP_WAITING) passiert immer und sofort: ``activate`` in
    // sw.js loescht nur alte Caches und ruft ``clients.claim()`` — IndexedDB
    // (Offline-Queue, Krypto-Key) bleibt unberuehrt. Ein wartender Worker
    // beweist ausserdem, dass ``install``/``cache.addAll`` durchlief. Die
    // Aktivierung kann also nichts verlieren, und nur sie macht die
    // precachten Shells frisch (Refs #1575).
    //
    // Gegated wird der RELOAD: er verwirft ungespeicherte Eingaben (nur
    // core/events/create.html hat data-autosave) und trifft via
    // ``clients.claim()`` JEDEN offenen Tab, nicht nur diesen. Fluechtiger
    // UI-Zustand (Scroll-Position, aufgeklappte Panels, HTMX-nachgeladene
    // Fragmente) ist bewusst NICHT Teil des Gates — geschuetzt sind genau
    // Formulareingaben (``_dirty``) und der Queue-Replay (``_queuePending``).
    //
    // Damit bleibt der Schutzzweck von #1386/#659 erhalten; das dortige
    // *Aktivierungs*-Gate entfaellt bewusst — es liess Geraete auf der alten
    // Shell haengen, sobald niemand den Toast anklickte.
    var _registration = null;
    var _dirty = false;
    var _userRequestedReload = false;

    // Ungespeicherte Eingaben erkennen. Capture-Phase, damit auch Felder in
    // HTMX-eingeswappten Fragmenten erfasst werden.
    document.addEventListener("input", _markDirty, true);
    document.addEventListener("change", _markDirty, true);

    function _markDirty() {
        _dirty = true;
    }

    if ("serviceWorker" in navigator) {
        // ``controllerchange`` feuert, sobald ein neuer SW die Seite
        // uebernimmt — der ``reloaded``-Guard verhindert eine Reload-Schleife,
        // falls der Browser das Event mehrfach ausloest.
        //
        // WICHTIG (``wasControlled``-Guard): Auch der ALLERERSTE SW-Install
        // feuert controllerchange — ``clients.claim()`` im activate-Handler
        // macht die bislang unkontrollierte Seite kontrolliert. Das ist KEIN
        // Update und darf nicht neu laden: Ein Reload in diesem Moment
        // bricht laufende Arbeit ab, konkret den fetch()-basierten
        // Login-POST von auth-bootstrap.js (net::ERR_ABORTED, Login
        // scheitert). Nur eine Seite, die schon vorher einen Controller
        // hatte, erlebt einen echten SW-Wechsel.
        var reloaded = false;
        var wasControlled = !!navigator.serviceWorker.controller;
        navigator.serviceWorker.addEventListener("controllerchange", function () {
            if (!wasControlled) {
                wasControlled = true;
                return;
            }
            _reloadIfSafe();
        });

        navigator.serviceWorker.register("/sw.js", { scope: "/" }).then(function (registration) {
            _registration = registration;

            // Refs #1593/#1575: Ein bereits WARTENDER Worker wird sonst nie
            // aktiviert — ``updatefound`` feuert nur in dem Tab, in dem der
            // Worker installiert wurde, und nur dieses eine Mal. Wer in dem
            // Moment nicht hinschaut (oder den Tab schliesst), haengt sonst
            // dauerhaft auf der alten Version fest.
            _activateWaiting(registration);

            // Update waehrend der Sitzung: installing → installed → waiting.
            registration.addEventListener("updatefound", function () {
                var newWorker = registration.installing;
                if (!newWorker) return;
                newWorker.addEventListener("statechange", function () {
                    if (newWorker.state === "installed" && navigator.serviceWorker.controller) {
                        _activateWaiting(registration);
                    }
                });
            });
        });

        navigator.serviceWorker.addEventListener("message", async function (event) {
            if (event.data.type === "QUEUE_REQUEST") {
                // ACK/NACK an den Service Worker zurueckschicken (#662).
                // Ohne ACK wartet der SW max. QUEUE_ACK_TIMEOUT_MS und liefert
                // dann eine 503-Response, damit der User keinen falschen
                // Erfolgs-Banner sieht.
                var port = event.ports && event.ports[0];
                if (!window.offlineQueue) {
                    if (port) port.postMessage({ type: "QUEUE_NACK", reason: "OfflineQueueNotLoaded" });
                    return;
                }
                try {
                    await window.offlineQueue.enqueueRequest(
                        event.data.url,
                        event.data.method,
                        event.data.body,
                        event.data.headers
                    );
                    if (port) port.postMessage({ type: "QUEUE_ACK" });
                } catch (e) {
                    // eslint-disable-next-line no-console
                    console.warn("[offline-queue]", e.message);
                    if (port) port.postMessage({ type: "QUEUE_NACK", reason: e.name || "EnqueueFailed" });
                }
            }
            // Refs #1351 (M6): REPLAY_QUEUE-Empfang entfernt. Replay-
            // Koordination bei Reconnect laeuft seit M6 ueber
            // sync-orchestrator.js (exklusiver Web Lock
            // "anlaufstelle-offline-mutex" + BroadcastChannel
            // "anlaufstelle-offline"), nicht mehr ueber den Service Worker.
        });
    }

    /**
     * Zeigt einen diskreten Hinweis-Toast "Neue Version verfügbar" unten
     * rechts im Viewport.
     *
     * Refs #1593: Der Toast ist der FALLBACK-Pfad. Der Regelfall ist die
     * stille Auto-Aktivierung samt Reload; hierher kommt nur, wessen Seite
     * nicht ungefragt neu geladen werden darf (ungespeicherte Eingaben oder
     * laufende Offline-Synchronisation). Der neue Worker ist dann bereits
     * aktiv — der Toast bietet nur noch das Nachziehen dieser einen Seite an.
     *
     * Kein Alert(), keine Modal-Overlay-Library — nur DOM-Manipulation,
     * kompatibel mit unserer CSP (kein Inline-Script, keine unsafe-*
     * Directives außer den bestehenden für Alpine).
     */
    // Refs #1412 (M17): Toast-Strings kommen als data-i18n-*-Attribute vom
    // <body> (base.html, {% trans %}) — kein hartkodiertes deutsches JS-Literal
    // mehr. Leerer Fallback (etabliertes Muster, vgl. client-row-offline.js),
    // falls das Attribut mal fehlt.
    function _i18n(key) {
        return (document.body && document.body.dataset[key]) || "";
    }

    /**
     * Fordert einen wartenden Worker zur Uebernahme auf (Refs #1593).
     * Bewusst ohne Bedingung: siehe Kommentarblock oben — die Aktivierung
     * ist immer verlustfrei, sie macht nur die Caches frisch.
     */
    function _activateWaiting(registration) {
        if (registration && registration.waiting && navigator.serviceWorker.controller) {
            registration.waiting.postMessage({ type: "SKIP_WAITING" });
        }
    }

    /**
     * Ist die Offline-Queue noch ungedrained? Dann nicht ungefragt neu laden —
     * ein Reload mitten im Replay bricht ihn ab. Auf Seiten ohne
     * offline-queue.js (login.html) gibt es keine Queue.
     *
     * Fehlerpfad: ``getQueueCount`` faengt IndexedDB-Fehler bereits selbst
     * ab und liefert dann 0 — in dem Fall wird also neu geladen. Vertretbar,
     * weil der Replay idempotent ist (Idempotenz-Keys) und ein Reload ihn nur
     * unterbricht, nicht verwirft. Der ``catch`` unten greift nur bei
     * unerwarteten Rejections ausserhalb dieses Kontrakts.
     */
    function _queuePending() {
        if (!window.offlineQueue || !window.offlineQueue.getQueueCount) {
            return Promise.resolve(false);
        }
        return window.offlineQueue
            .getQueueCount()
            .then(function (count) {
                return count > 0;
            })
            .catch(function () {
                return true;
            });
    }

    function _doReload() {
        if (reloaded) return;
        reloaded = true;
        window.location.reload();
    }

    /**
     * Der neue SW kontrolliert die Seite bereits (Caches sind frisch). Jetzt
     * nur noch entscheiden, ob wir die Seite dafuer wegreissen duerfen.
     * Ein ausdruecklicher Klick auf "Neu laden" schlaegt jeden Guard.
     */
    function _reloadIfSafe() {
        if (reloaded) return;
        if (_userRequestedReload) {
            _doReload();
            return;
        }
        if (_dirty) {
            _showUpdatePrompt(_registration);
            return;
        }
        _queuePending().then(function (pending) {
            if (pending) {
                _showUpdatePrompt(_registration);
            } else {
                _doReload();
            }
        });
    }

    function _showUpdatePrompt(registration) {
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
        // Refs #1386: Tailwinds Content-Scan umfasst src/static/js/ nicht
        // (tailwind.config.js: nur templates/**.html + core/**.py) — Klassen,
        // die NUR hier im JS vorkommen (bottom-4, bg-indigo-600), fehlen im
        // kompilierten styles.css. Ohne Fallback rendert der Toast unterhalb
        // des Viewports (fixed ohne bottom = statische Position am
        // Dokumentende) und ohne Hintergrund. Die positions-/
        // sichtbarkeitskritischen Properties daher zusaetzlich inline
        // (CSSOM-Manipulation, von der CSP nicht betroffen); die Klassen
        // bleiben fuehrend, falls der Scan spaeter JS einschliesst.
        toast.style.position = "fixed";
        toast.style.bottom = "1rem";
        toast.style.right = "1rem";
        toast.style.zIndex = "50";
        toast.style.backgroundColor = "#4f46e5"; // indigo-600

        var text = document.createElement("span");
        text.className = "text-sm flex-grow";
        text.textContent = _i18n("i18nSwUpdate");
        toast.appendChild(text);

        var reloadBtn = document.createElement("button");
        reloadBtn.type = "button";
        reloadBtn.className =
            "text-sm font-semibold bg-white text-indigo-700 px-3 py-1 " +
            "rounded hover:bg-indigo-50";
        reloadBtn.textContent = _i18n("i18nSwReload");
        reloadBtn.addEventListener("click", function () {
            // Ausdrueckliche Bestaetigung — schlaegt Dirty-/Queue-Guard.
            _userRequestedReload = true;
            if (registration && registration.waiting) {
                // Noch nicht uebernommen: erst aktivieren, der Reload folgt
                // im controllerchange-Handler (dann serviert der NEUE Worker).
                registration.waiting.postMessage({ type: "SKIP_WAITING" });
                return;
            }
            // Refs #1593: Der Worker ist bereits aktiv (Auto-Aktivierung), die
            // Seite wurde nur wegen Eingaben/Queue nicht neu geladen —
            // ``registration.waiting`` ist null und controllerchange kommt
            // nicht mehr. Ohne diesen Zweig waere der Button wirkungslos.
            _doReload();
        });
        toast.appendChild(reloadBtn);

        var dismissBtn = document.createElement("button");
        dismissBtn.type = "button";
        dismissBtn.setAttribute("aria-label", _i18n("i18nSwDismiss"));
        dismissBtn.className = "text-white/70 hover:text-white text-lg leading-none";
        dismissBtn.textContent = "×";
        dismissBtn.addEventListener("click", function () {
            toast.remove();
        });
        toast.appendChild(dismissBtn);

        document.body.appendChild(toast);
    }

    function _wipeOfflineState() {
        // Refs #1065: gemeinsamer Wipe-Pfad lebt in crypto.js — Logout
        // (hier) und Idle-Timeout (crypto.js) räumen identisch ab:
        // CryptoKey + Aktivitäts-Stempel + alle Dexie-Tabellen.
        try {
            if (window.crypto_session) {
                window.crypto_session.wipeOfflineState();
            } else if (window.offlineStore) {
                window.offlineStore.purgeAll();
            }
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
        (async function () {
            try {
                var cs = window.crypto_session;
                if (cs && cs.ready) {
                    await cs.ready();
                }
                // Refs #1352: ohne Schluessel keine Loeschentscheidung — nach
                // Idle-Lock (#1324) oder vor dem ersten Re-Login waere jeder
                // Decrypt-Fehler nur transient (NoSessionKeyError); der
                // Boot-Purge wuerde also nichts sinnvoll aufraeumen. Skip
                // statt Purge ohne Key.
                var hasKey = cs && cs.hasSessionKey ? cs.hasSessionKey() : false;
                if (hasKey) {
                    await window.offlineStore.purgeExpired();
                }
            } catch (_e) {
                // ignore — better to leave stale records than crash
            }
        })();
    }
})();
