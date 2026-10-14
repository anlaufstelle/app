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
    var _shellLanguageOverride = null;
    var _shellLanguagePending = false;
    var _dirty = false;
    var _userRequestedReload = false;

    // Refs #1951: Fenster, in dem eine bereits angestossene Logout-Navigation
    // jede andere clientseitige Navigation verdraengt.
    //
    // Bewusst ein ZEITSTEMPEL und kein Boolean: die Marke wird nie
    // zurueckgesetzt (im Normalfall ersetzt die Navigation die Seite, es gibt
    // also kein "danach"). Ohne Deckel bliebe ein Dokument, dessen Logout
    // NICHT navigiert hat — etwa weil die Nav-Form waehrend des Wipe-Fensters
    // aus dem DOM verschwand und ``form.submit()`` ins Leere lief — fuer den
    // Rest seiner Lebensdauer beschaedigt: der Toast-Knopf "Neu laden" waere
    // tot und zeitstrom-live-watch.js wuerde bei einem spaeteren
    // Session-Ablauf nie mehr von der sensiblen Seite weg navigieren.
    // Ein Zeitstempel heilt das von selbst und ist gegenueber einem
    // ``setTimeout``-Reset robust, den Hintergrund-Tab-Throttling beliebig
    // weit verschleppen kann.
    var _LOGOUT_NAV_GRACE_MS = 10000;

    function _logoutNavInFlight() {
        var startedAt = window.__logoutInFlight;
        return typeof startedAt === "number" && Date.now() - startedAt < _LOGOUT_NAV_GRACE_MS;
    }

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
        // Refs #1951 (``controller``-Verlust-Guard): controllerchange feuert
        // AUCH, wenn die Registrierung verschwindet — dann ist ``controller``
        // danach ``null``. Genau das passiert bei jedem Logout: die Antwort
        // auf ``POST /logout/`` traegt ``Clear-Site-Data: "storage"`` (siehe
        // ``CustomLogoutView.dispatch``), und Chromium raeumt damit auch die
        // SW-Registrierung ab — waehrend die Weiterleitung nach ``/login/``
        // noch fliegt. Ein Reload in diesem Moment BRICHT diese Navigation AB
        // (net::ERR_ABORTED) und laedt stattdessen die alte, jetzt
        // unauthentifizierte URL neu; der Nutzer landet auf
        // ``/login/?next=<alte URL>`` statt auf ``/login/`` — als ``next``
        // sogar mit einer gerade fliegenden HTMX-Partial-URL.
        //
        // Ein verlorener Controller ist ohnehin nie ein Update: es gibt keine
        // frischen Caches, fuer die sich ein Reload lohnen wuerde. Wir setzen
        // ``wasControlled`` zurueck — die Seite ist jetzt unkontrolliert, und
        // eine spaetere Uebernahme ist damit wieder der (reload-freie)
        // Erst-Kontroll-Fall von oben.
        var reloaded = false;
        var wasControlled = !!navigator.serviceWorker.controller;
        navigator.serviceWorker.addEventListener("controllerchange", function () {
            _sendShellLanguage(navigator.serviceWorker.controller);
            if (!navigator.serviceWorker.controller) {
                wasControlled = false;
                return;
            }
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

            // Refs #1948: Precache-Selbstheilung anstossen — einmal pro
            // Seitenladen (siehe _requestPrecacheHeal unten).
            _requestPrecacheHeal(registration);
            _sendShellLanguage(registration.active);

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

    function _sendShellLanguage(worker) {
        // Public shells must not override the account; logout is explicit.
        if (_shellLanguageOverride === null && !document.body.dataset.userId) return;
        var language = _shellLanguageOverride || document.documentElement.lang;
        if (language !== "de" && language !== "en") return;
        _shellLanguagePending = true;
        if (!worker) return;
        try {
            worker.postMessage({ type: "SET_SHELL_LANGUAGE", language: language });
            _shellLanguagePending = false;
        } catch (error) {
            console.warn("[offline] Shell language message failed", error);
        }
    }

    window.addEventListener("online", function () {
        if (_shellLanguagePending) _sendShellLanguage(navigator.serviceWorker?.controller || _registration?.active);
    });

    /**
     * Refs #1948: Precache-Selbstheilung anstossen — CHECK_PRECACHE an den
     * AKTIVEN Worker (sw.js prueft darauf jeden APP_SHELL-Eintrag per
     * URL-genauem cache.match und laedt NUR Fehlendes nach). Vom Dokument aus
     * angestossen, weil install/activate auf einem Bestandsgeraet nie wieder
     * laufen, waehrend der Browser die Cache Storage unter Speicherdruck
     * trotzdem (teil-)leeren darf — bisher verlor das Geraet dann still seine
     * Offline-Faehigkeit bis zum naechsten CACHE_NAME-Bump.
     *
     * Beim Erst-Install ist ``registration.active`` noch null — bewusst kein
     * Send: install hat den Precache gerade selbst befuellt. Best-effort;
     * der naechste Seitenaufruf stoesst erneut an.
     */
    function _requestPrecacheHeal(registration) {
        var worker = registration && registration.active;
        if (!worker || typeof worker.postMessage !== "function") return;
        try {
            worker.postMessage({ type: "CHECK_PRECACHE" });
        } catch (_e) {
            // still — best-effort (s.o.).
        }
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
        // Refs #2360: der Aufrufer prueft die Logout-Marke ggf. VOR einem
        // asynchronen Zwischenschritt (_queuePending()) — startet ein
        // Schnell-Verlassen erst waehrend dieses Wartens, bekommt der Reload
        // die Marke sonst nie zu sehen. Deshalb hier, unmittelbar vor dem
        // tatsaechlichen Reload, ein letztes Mal pruefen; das deckt zugleich
        // den direkten Aufruf aus dem ``_userRequestedReload``-Pfad ab, ohne
        // den bestehenden Guard in ``_reloadIfSafe`` zu duplizieren.
        if (_logoutNavInFlight()) return;
        reloaded = true;
        window.location.reload();
    }

    /**
     * Der neue SW kontrolliert die Seite bereits (Caches sind frisch). Jetzt
     * nur noch entscheiden, ob wir die Seite dafuer wegreissen duerfen.
     *
     * Vorrangordnung (Refs #1951): Eine laufende Logout-Navigation schlaegt
     * ALLES — auch einen ausdruecklichen Klick auf "Neu laden". Sie ist
     * zeitlich gedeckelt (``_LOGOUT_NAV_GRACE_MS``), danach gilt wieder: ein
     * ausdruecklicher Klick auf "Neu laden" schlaegt jeden weiteren Guard.
     */
    function _reloadIfSafe() {
        if (reloaded) return;
        // Refs #1951: Die Weiterleitung nach ``/login/`` darf nicht
        // abgebrochen werden — die Seite ist ohnehin gleich weg.
        if (_logoutNavInFlight()) return;
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

    // Refs #1476 (R2): harter Deckel, damit der Logout NIE am Wipe haengen
    // bleibt (blockierte IndexedDB, klemmender Purge o.ae.).
    var _LOGOUT_WIPE_TIMEOUT_MS = 3000;

    function _wipeOfflineState() {
        // Retain logout's reset for a retry even while the old page is open.
        _shellLanguageOverride = "de";
        _sendShellLanguage(navigator.serviceWorker?.controller || _registration?.active);
        // Refs #1065: gemeinsamer Wipe-Pfad lebt in crypto.js — Logout
        // (hier) und Idle-Timeout (crypto.js) räumen identisch ab:
        // CryptoKey + Aktivitäts-Stempel + alle Dexie-Tabellen.
        //
        // Refs #1476: Gibt IMMER ein Promise zurueck, das erfuellt, wenn der
        // (asynchrone) Purge durch ist — der Logout-Handler serialisiert den
        // nativen POST dahinter. ``Promise.resolve`` umschliesst sowohl das
        // echte async-Ergebnis als auch den No-Store-Fall.
        try {
            if (window.crypto_session) {
                return Promise.resolve(window.crypto_session.wipeOfflineState());
            }
            if (window.offlineStore) {
                return Promise.resolve(window.offlineStore.purgeAll());
            }
        } catch (_e) {
            // synchroner Wurf (z.B. TypeError) — trotzdem sauber aufloesen,
            // damit der Logout unten in jedem Fall abgeschickt wird.
        }
        return Promise.resolve();
    }

    // Hook every logout-form submit. Both desktop and mobile nav use a
    // POST form to {% url 'logout' %} — we match by action attribute.
    //
    // Refs #1476 (R2): Der Wipe war fire-and-forget — der native Logout-POST
    // rannte gegen den asynchronen IndexedDB-Purge, und auf Browsern ohne
    // Clear-Site-Data-Support ueberlebten Schluessel + PII sichtbar auf der
    // oeffentlichen /offline/-Seite. Wir halten den POST jetzt an
    // (preventDefault), lassen den Wipe fertig laufen (mit hartem
    // Timeout-Fallback, damit der Logout nie haengt) und loesen ihn erst
    // DANACH programmatisch aus. ``form.submit()`` feuert das submit-Event
    // NICHT erneut — genau richtig hier; der Re-Entrancy-Guard schuetzt nur
    // gegen einen zweiten Nutzer-Klick, waehrend der Wipe noch laeuft.
    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (!form || form.tagName !== "FORM") return;
        var action = form.getAttribute("action") || "";
        if (action.indexOf("/logout/") === -1 && !action.endsWith("/logout/")) return;

        // Re-Entrancy-Guard pro Form: der zweite Klick darf nicht am
        // laufenden Wipe vorbei nativ abschicken.
        if (form._logoutWipeInFlight) {
            event.preventDefault();
            return;
        }
        form._logoutWipeInFlight = true;
        // Refs #1951: Ab HIER laeuft der Logout — nicht erst ab
        // ``form.submit()``. Dazwischen liegt der Wipe (bis zu
        // ``_LOGOUT_WIPE_TIMEOUT_MS``), in dem der native POST bereits
        // unterbunden ist, aber noch nichts navigiert. Ein ``_doReload()`` in
        // genau diesem Fenster wuerde den Logout NICHT nur verzoegern, sondern
        // ganz verwerfen: der POST kaeme nie, der Nutzer bliebe angemeldet —
        // waehrend der Wipe seine Offline-Daten schon geloescht hat. Deshalb
        // deckt die Marke die ganze Spanne ab (Klick bis Navigation).
        //
        // Vertrag ist ein ZEITSTEMPEL in Millisekunden, kein Boolean, und die
        // Leser deuten ihn nur ``_LOGOUT_NAV_GRACE_MS`` lang als "laeuft".
        // Zurueckgenommen wird die Marke nie — im Normalfall ersetzt die
        // Navigation die Seite. Der Deckel ist die Selbstheilung fuer den
        // Fall, dass es NICHT dazu kommt (z.B. ``form.submit()`` laeuft ins
        // Leere, weil die Nav-Form im Wipe-Fenster aus dem DOM verschwand):
        // ohne ihn blieben der Toast-Knopf "Neu laden" und das Weg-Navigieren
        // in zeitstrom-live-watch.js fuer die Restlebensdauer des Dokuments
        // tot. Die Marke liegt auf ``window``, weil sie ausserhalb dieses
        // Moduls gelesen wird (``leaveSensitivePage``).
        window.__logoutInFlight = Date.now();

        event.preventDefault();

        var timeout = new Promise(function (resolve) {
            setTimeout(resolve, _LOGOUT_WIPE_TIMEOUT_MS);
        });

        // Fehlerpfad: Wirft der Wipe, wird trotzdem ausgeloggt (``catch`` ->
        // resolved). Haengt er, gewinnt der Timeout via ``Promise.race``.
        Promise.race([
            _wipeOfflineState().catch(function () {
                // ignore — Rest-Chiffrat ist ohne Schluessel unlesbar (F-01)
            }),
            timeout,
        ]).then(function () {
            // Refs #1951: Marke auffrischen — der Deckel soll ab der
            // NAVIGATION laufen, nicht ab dem Klick. Sonst frisst ein langer
            // Wipe (bis 3 s) ein Drittel des Fensters auf.
            window.__logoutInFlight = Date.now();
            form.submit();
        });
    });

    // Bootstrap: on every page load, drop expired records (TTL handling).
    // Salt + key derivation happens in login.html / password_change.html —
    // those templates trigger crypto_session.deriveSessionKey before
    // redirecting away from the login page.
    //
    // Refs #1966: Das Gate hing frueher zusaetzlich an
    // ``document.body.dataset.userId``. Die precachten Offline-Shells tragen
    // dieses Attribut nicht mehr (es fror die Nutzer-pk im
    // Service-Worker-Cache ein) — ein PWA-Kaltstart direkt in eine Shell
    // haette den Purge damit still uebersprungen und abgelaufene, aber
    // verschluesselte Bestaende ueber ihre Aufbewahrungsfrist hinaus liegen
    // lassen. Das eigentliche Auth-Signal ist ohnehin der Session-Key unten
    // (``hasKey``, Refs #1352); die Markup-Ableitung war nur ein Vorfilter.
    if (window.offlineStore) {
        (async function () {
            try {
                var cs = window.crypto_session;
                await cs?.awaitReady?.();
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
