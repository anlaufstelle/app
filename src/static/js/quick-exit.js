/*
 * Mitschauen-/Shoulder-Surfing-Schutz (Refs #2136, Befund N-2).
 *
 * Zwei getrennte Funktionen in einer Datei, weil sie sich dieselbe
 * Sichtblende und dieselbe Konfiguration teilen:
 *
 * 1. "Schnell verlassen" — Knopf in der Kopfleiste, Ctrl/Cmd+Shift+X oder
 *    Shift+Esc. Reihenfolge ist bewusst (1) verdecken, (2) abmelden,
 *    (3) navigieren: die Person, die mitliest, steht neben dem Geraet, nicht
 *    vor dem Server. Auf die Logout-Antwort wird NICHT gewartet — sie kommt
 *    an, waehrend die neue Seite schon laedt (``sendBeacon``/``keepalive``).
 * 2. Sichtschutz bei Fokusverlust (opt-in je Einrichtung) — legt bei
 *    ``blur``/``visibilitychange`` eine deckende Flaeche ueber den Inhalt.
 *    Freigabe nur per Klick oder Taste, KEIN Timer (WCAG 2.2.1) und keine
 *    Bewegung.
 *
 * WARUM EIGENE DATEI (statt einer Alpine-Komponente in base-layout.js):
 * Die precachten Offline-Shells erben ``offline_base.html`` und muessen
 * byte-gleich bleiben (Refs #1966) — sie duerfen also keine Facility-Werte
 * serverseitig rendern. Diese Datei laeuft dort mit demselben statischen
 * Script-Tag und liest die Konfiguration aus ``localStorage``, wo die
 * ONLINE-Seiten sie hinterlegt haben. Ohne Spiegel greifen die Vorgaben
 * (Standardziel, Sichtschutz aus).
 *
 * KEIN Audit-Ereignis und kein Zaehler: Zweck der Aktion ist der Schutz der
 * Person, nicht die Rechenschaft ueber ihr Verhalten.
 */
(function () {
    "use strict";

    // Muss mit ``core.models.settings.DEFAULT_QUICK_EXIT_URL`` uebereinstimmen
    // (Drift-Guard: src/tests/test_quick_exit.py). Die Offline-Shell hat
    // keinen Server, der die Vorgabe rendern koennte — deshalb steht sie hier.
    var DEFAULT_URL = "https://www.wikipedia.org/";
    var STORAGE_KEY = "anlaufstelle.quickExit";
    // R-L3-9: Deckel fuer das Warten auf die persistente Schluessel-Loeschung.
    // Lang genug fuer einen IndexedDB-Roundtrip, kurz genug, dass ein Haenger
    // die Flucht nicht aufhaelt (die Blende liegt in dieser Zeit bereits).
    var KEY_DROP_TIMEOUT_MS = 500;
    // R-L3-12: dreiwertiges Ergebnis der Loeschung. Der Unterschied ist NICHT
    // kosmetisch — der ``key-cleared``-Empfaenger in crypto.js verankert einen
    // noch vorhandenen Schluessel wieder (``cachedKey = row.cryptoKey``). Ein
    // Broadcast nach Timeout oder Fehler waere also schlimmer als keiner: er
    // koennte einem Fremd-Tab den bereits verworfenen Memory-Key
    // zurueckgeben.
    var KEY_DROP_CLEARED = "cleared";
    var KEY_DROP_TIMEOUT = "timeout";
    var KEY_DROP_ERROR = "error";

    function _body() {
        return document.body;
    }

    /** Nur http/https navigieren — ``javascript:``/``data:`` waeren XSS.
     *
     * Der Wert kommt zwar aus einem validierten Settings-Feld, laeuft aber
     * ueber ``localStorage`` (same-origin beschreibbar) in die Offline-Shell.
     * Die zweite Pruefung kostet nichts und schliesst genau diesen Umweg.
     */
    function _isNavigableUrl(value) {
        if (!value) return false;
        try {
            var parsed = new URL(value, window.location.href);
            return parsed.protocol === "http:" || parsed.protocol === "https:";
        } catch (_e) {
            return false;
        }
    }

    function _readMirror() {
        try {
            var raw = window.localStorage.getItem(STORAGE_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch (_e) {
            return null;
        }
    }

    function _writeMirror(config) {
        try {
            window.localStorage.setItem(
                STORAGE_KEY,
                JSON.stringify({ url: config.url, privacyScreen: config.privacyScreen })
            );
        } catch (_e) {
            // Privater Modus / abgeschaltete Speicherung: die Online-Seite
            // funktioniert weiter, offline greifen die Vorgaben.
        }
    }

    /** Konfiguration: Online aus den ``data-``-Attributen, offline aus dem Spiegel. */
    function _config() {
        var body = _body();
        var dataset = body ? body.dataset : {};
        var hasServerConfig = typeof dataset.quickExitUrl === "string";
        if (hasServerConfig) {
            var config = {
                url: _isNavigableUrl(dataset.quickExitUrl) ? dataset.quickExitUrl : DEFAULT_URL,
                privacyScreen: dataset.privacyScreen === "1",
            };
            _writeMirror(config);
            return config;
        }
        var mirror = _readMirror();
        return {
            url: mirror && _isNavigableUrl(mirror.url) ? mirror.url : DEFAULT_URL,
            privacyScreen: !!(mirror && mirror.privacyScreen),
        };
    }

    function _logoutUrl() {
        var body = _body();
        return (body && body.dataset.quickExitLogoutUrl) || "";
    }

    function _overlay() {
        return document.getElementById("privacy-screen");
    }

    function _overlayPanel() {
        return document.getElementById("privacy-screen-panel");
    }

    function _resumeButton() {
        return document.getElementById("privacy-screen-resume");
    }

    /** Alles unter der Blende: jedes direkte body-Kind ausser ihr selbst.
     *
     * Bewusst nicht die Aufzaehlung "main + nav + Leiste": Der Skip-Link ist
     * ebenfalls ein direktes body-Kind und bliebe sonst fokussierbar — die
     * Blende haette ein Loch, durch das die Tabulator-Taste hinausfuehrt. Eine
     * generische Regel deckt ausserdem alles ab, was spaeter dazukommt
     * (Banner, Consent-Leiste, weitere Layout-Bausteine).
     */
    function _shieldedRegions() {
        var overlay = _overlay();
        return Array.prototype.filter.call(document.body.children, function (element) {
            return element !== overlay && element.tagName !== "SCRIPT";
        });
    }

    var _supportsInert = typeof HTMLElement !== "undefined" && "inert" in HTMLElement.prototype;

    /** Inhalt unter der Blende stilllegen.
     *
     * ``visibility: hidden`` ist der eigentliche Riegel: Es nimmt den Inhalt
     * aus der Darstellung, aus der Tab-Reihenfolge UND aus dem
     * Accessibility-Baum. Ein blosses Overlay davor waere nur optisch — bei
     * "Schnell verlassen" muss der Inhalt auch dann weg sein, wenn die
     * Navigation haengt. ``inert`` kommt zusaetzlich obendrauf (und ist der
     * einzige Traeger, wo es unterstuetzt wird); wo es fehlt, uebernimmt
     * ``aria-hidden`` die Rolle im Accessibility-Baum. Fokussierbare Reste in
     * einem ``aria-hidden``-Teilbaum kann es dabei nicht geben — die
     * Sichtbarkeit ist ja gleichzeitig weg (axe: aria-hidden-focus).
     */
    function _shield(active) {
        _shieldedRegions().forEach(function (region) {
            if (_supportsInert) {
                region.inert = active;
            } else if (active) {
                region.setAttribute("aria-hidden", "true");
            } else {
                region.removeAttribute("aria-hidden");
            }
            region.style.visibility = active ? "hidden" : "";
        });
    }

    var _lastFocused = null;

    function _showOverlay(mode) {
        var overlay = _overlay();
        if (!overlay) return;
        var panel = _overlayPanel();
        // Beim Verlassen bleibt die Flaeche leer: die Aufforderung
        // "Zum Fortfahren ..." waere dort falsch — es geht gerade nicht weiter.
        if (panel) panel.hidden = mode === "exit";
        overlay.dataset.mode = mode;
        overlay.hidden = false;
    }

    function _hideOverlay() {
        var overlay = _overlay();
        if (!overlay) return;
        overlay.hidden = true;
        delete overlay.dataset.mode;
    }

    function _isOverlayVisible() {
        var overlay = _overlay();
        return !!overlay && !overlay.hidden;
    }

    // --- Sichtschutz -------------------------------------------------------

    function armPrivacyScreen() {
        if (_isOverlayVisible()) return;
        _lastFocused = document.activeElement;
        _showOverlay("privacy");
        _shield(true);
        var resume = _resumeButton();
        if (resume) resume.focus();
    }

    /** WCAG 2.4.3: Fokus zurueck auf das Element, das ihn vor der Blende hatte. */
    function releasePrivacyScreen() {
        var overlay = _overlay();
        if (!overlay || overlay.dataset.mode !== "privacy") return;
        _hideOverlay();
        _shield(false);
        var previous = _lastFocused;
        _lastFocused = null;
        if (previous && typeof previous.focus === "function" && document.contains(previous)) {
            previous.focus();
        }
    }

    // --- Schnell verlassen -------------------------------------------------

    /** Abmelden ohne auf die Antwort zu warten.
     *
     * ``sendBeacon`` mit ``application/x-www-form-urlencoded``-Blob: der
     * CSRF-Token muss im Body stehen, weil ein Beacon keine eigenen Header
     * setzen kann und der Cookie ``HttpOnly`` ist (Refs #602). Fehlt
     * ``sendBeacon`` (oder lehnt es die Uebergabe ab), uebernimmt ein
     * ``fetch`` mit ``keepalive`` — dieselbe Zusage, die Anfrage ueberlebt
     * die Navigation.
     */
    function _logout() {
        var url = _logoutUrl();
        if (!url) return false; // Offline-Shell: kein Server erreichbar.
        var token = (typeof window.getCsrfToken === "function" && window.getCsrfToken()) || "";
        var body = "csrfmiddlewaretoken=" + encodeURIComponent(token);
        if (navigator.sendBeacon) {
            var blob = new Blob([body], { type: "application/x-www-form-urlencoded" });
            if (navigator.sendBeacon(url, blob)) return true;
        }
        try {
            window.fetch(url, {
                method: "POST",
                keepalive: true,
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-CSRFToken": token,
                },
                body: body,
            });
        } catch (_e) {
            // Auch ein gescheiterter Logout darf die Navigation nicht
            // aufhalten — sichtbar ist am Geraet nur die neue Seite.
            return false;
        }
        return true;
    }

    /** Sitzungsschluessel verwerfen — und zwar FERTIG, bevor navigiert wird.
     *
     * ``clearSessionKey`` nullt den RAM-Key synchron, loescht die persistente
     * Zeile aber erst nach einem asynchronen IndexedDB-Open und zwei
     * abgewarteten Deletes (crypto.js). Reisst ``location.replace`` den
     * Ausfuehrungskontext vorher weg, ist die Loeschung nicht garantiert — die
     * naechste same-origin-Shell koennte den Schluessel wieder laden und die
     * Offline-Daten damit erneut entschluesseln. Offline ist das der EINZIGE
     * wirksame Riegel (ein Server-Logout gibt es dort nicht).
     *
     * Gewartet wird mit Deckel (R-L3-9): haengt IndexedDB, gewinnt der Timer
     * und es wird trotzdem navigiert. Das ist vertretbar, weil die Blende zu
     * diesem Zeitpunkt bereits liegt — der Bildschirm ist geschuetzt, auch
     * wenn die Aufraeumarbeit noch laeuft.
     *
     * Liefert (R-L3-12) ``KEY_DROP_CLEARED`` / ``KEY_DROP_TIMEOUT`` /
     * ``KEY_DROP_ERROR`` statt nur "fertig". Nur der erste Fall ist ein
     * NACHWEIS, dass die persistente Zeile weg ist — und nur er darf den
     * Cross-Tab-Broadcast ausloesen.
     *
     * ``KEY_DROP_CLEARED`` verlangt seit R-L3-13 ZWEI Belege: die ausdrueckliche
     * Erfolgsmeldung von ``clearSessionKey()`` UND eine leere Rueckleseprobe
     * (``hasPersistedSessionKey()``). Der Deckel umspannt beide Schritte.
     */
    function _dropSessionKey() {
        var session = window.crypto_session;
        if (!session || typeof session.clearSessionKey !== "function") {
            // Kein Krypto-Modul (Testharness, abgebrochener Ladevorgang): Wir
            // haben nichts geloescht und duerfen es auch nicht behaupten.
            return Promise.resolve(KEY_DROP_ERROR);
        }
        return Promise.race([
            _clearAndVerify(session),
            new Promise(function (resolve) {
                window.setTimeout(function () {
                    resolve(KEY_DROP_TIMEOUT);
                }, KEY_DROP_TIMEOUT_MS);
            }),
        ]);
    }

    /** Loeschen und den Erfolg NACHLESEN — kein Verlass auf die Zusage allein.
     *
     * Zwei Stufen, beide noetig (R-L3-13):
     *   1. ``clearSessionKey()`` meldet seit Refs #2136 ``{persisted: true}``
     *      nur, wenn der IndexedDB-Delete des Schluessels wirklich durchlief.
     *      Alles andere — eine Rejection, eine synchrone Rueckkehr (also gar
     *      kein Promise), ein fehlendes oder falsches Ergebnisobjekt — heisst
     *      "nicht nachgewiesen".
     *   2. Danach wird die Zeile zurueckgelesen. Erst wenn sie nachweislich
     *      FEHLT, gilt die Loeschung als belegt. Diese zweite Stufe faengt
     *      auch den Fall ab, dass eine aeltere/fremde crypto.js-Fassung
     *      ``{persisted:true}`` meldet, ohne es halten zu koennen.
     */
    function _clearAndVerify(session) {
        var pending;
        try {
            pending = session.clearSessionKey();
        } catch (_e) {
            return Promise.resolve(KEY_DROP_ERROR);
        }
        if (!pending || typeof pending.then !== "function") {
            // Synchrone Rueckkehr: es gibt nichts, worauf man warten koennte,
            // und damit auch keinen Nachweis.
            return Promise.resolve(KEY_DROP_ERROR);
        }
        return Promise.resolve(pending).then(function (outcome) {
            if (!outcome || outcome.persisted !== true) return KEY_DROP_ERROR;
            return _confirmKeyRowGone(session);
        }, function () {
            return KEY_DROP_ERROR;
        });
    }

    /** Rueckleseprobe: liegt die Schluesselzeile wirklich nicht mehr da? */
    function _confirmKeyRowGone(session) {
        if (typeof session.hasPersistedSessionKey !== "function") return KEY_DROP_ERROR;
        var probe;
        try {
            probe = session.hasPersistedSessionKey();
        } catch (_e) {
            return KEY_DROP_ERROR;
        }
        if (!probe || typeof probe.then !== "function") return KEY_DROP_ERROR;
        return Promise.resolve(probe).then(function (stillThere) {
            return stillThere ? KEY_DROP_ERROR : KEY_DROP_CLEARED;
        }, function () {
            return KEY_DROP_ERROR;
        });
    }

    /** Fehlgeschlagene Loeschung sichtbar machen, ohne die Flucht aufzuhalten. */
    function _warnKeyDropFailed(status) {
        try {
            if (!window.console || typeof window.console.warn !== "function") return;
            window.console.warn(
                status === KEY_DROP_TIMEOUT
                    ? "Schnell verlassen: Die Loeschung des lokalen Sitzungsschluessels war nach " +
                          KEY_DROP_TIMEOUT_MS +
                          " ms nicht abgeschlossen. Es wird trotzdem verlassen; der Schluessel kann bis zum " +
                          "naechsten Abmelden, Idle-Wipe oder Ablauf der Offline-Berechtigung bestehen bleiben."
                    : "Schnell verlassen: Die Loeschung des lokalen Sitzungsschluessels ist nicht nachgewiesen " +
                          "(Fehler oder Zeile weiterhin vorhanden). Es wird trotzdem verlassen; der Schluessel kann " +
                          "bis zum naechsten Abmelden, Idle-Wipe oder Ablauf der Offline-Berechtigung bestehen bleiben."
            );
        } catch (_e) {
            // ignore — eine Warnung darf die Flucht nicht aufhalten.
        }
    }

    /** Anderen Tabs sagen, dass ihr Memory-Key ohne Grundlage dasteht.
     *
     * Derselbe Kanal, den crypto.js nach dem Idle-Wipe nutzt
     * (``syncOrchestrator.broadcast({type:"key-cleared"})``). Der Empfaenger in
     * crypto.js verwirft NUR seinen Memory-Key und prueft vorher, ob die
     * IndexedDB-Zeile wirklich fehlt — steht sie noch, VERANKERT er seinen
     * Schluessel stattdessen neu (``cachedKey = row.cryptoKey``). Deshalb wird
     * ausschliesslich nach einem nachgewiesenen ``KEY_DROP_CLEARED`` gesendet:
     * ein Broadcast nach Timeout oder Fehler koennte einem Fremd-Tab den
     * bereits verworfenen Schluessel zurueckgeben (R-L3-12).
     *
     * Der gerenderte DOM eines zweiten Tabs bleibt trotzdem stehen: Quick-Exit
     * wirkt pro Fenster (siehe Restrisiken in docs/threat-model.md).
     */
    function _broadcastKeyCleared() {
        try {
            var orchestrator = window.syncOrchestrator;
            if (orchestrator && typeof orchestrator.broadcast === "function") {
                orchestrator.broadcast({ type: "key-cleared" });
            }
        } catch (_e) {
            // best-effort — ohne BroadcastChannel ist das ohnehin ein No-op.
        }
    }

    /** Gibt ein Promise zurueck, das nach der Navigation aufloest (fuer Tests). */
    function quickExit() {
        var target = _config().url;
        _showOverlay("exit");
        _shield(true);
        _logout();
        return _dropSessionKey().then(function (status) {
            if (status === KEY_DROP_CLEARED) {
                _broadcastKeyCleared();
            } else {
                _warnKeyDropFailed(status);
            }
            window.location.replace(target);
        });
    }

    // --- Verdrahtung -------------------------------------------------------

    function _isQuickExitShortcut(event) {
        var key = event.key || "";
        if ((event.ctrlKey || event.metaKey) && event.shiftKey && key.toLowerCase() === "x") return true;
        // Shift+Esc als Alternative — reines Esc bliebe ein
        // Character-Key-Shortcut (WCAG 2.1.4) und kollidierte mit dem
        // Schliessen der mobilen Suche (@keydown.escape.window).
        return event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey && (key === "Escape" || key === "Esc");
    }

    function _onKeydown(event) {
        if (_isQuickExitShortcut(event)) {
            // Capture-Phase am window: das Kuerzel wirkt auch aus einem
            // fokussierten Eingabefeld heraus und vor jedem Alpine-Handler.
            //
            // ``stopImmediatePropagation`` zusaetzlich zu ``preventDefault``:
            // Das Kuerzel muss EXKLUSIV konsumiert werden. ``preventDefault``
            // unterdrueckt nur die Standardaktion, nicht weitere Listener — die
            // mobile Suche haengt mit ``@keydown.escape.window`` am selben
            // ``window`` und wuerde bei Umschalt+Esc mitfeuern (Dropdown
            // schliessen, Fokus umsetzen), waehrend die Flucht laeuft. Plain
            // ``Esc`` ist davon nicht betroffen: dieser Zweig greift nur bei
            // den beiden Modifier-Kuerzeln.
            event.preventDefault();
            event.stopImmediatePropagation();
            quickExit();
            return;
        }
        if (!_isOverlayVisible()) return;
        if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
            // Dieselbe Exklusivitaet fuer die Freigabe: liegt die Blende, ist
            // sie modal — kein anderer Listener darf denselben Tastendruck
            // zusaetzlich verarbeiten.
            event.preventDefault();
            event.stopImmediatePropagation();
            releasePrivacyScreen();
        }
    }

    function init() {
        var config = _config();

        var button = document.getElementById("quick-exit-button");
        if (button) button.addEventListener("click", quickExit);

        var overlay = _overlay();
        if (overlay) overlay.addEventListener("click", releasePrivacyScreen);

        window.addEventListener("keydown", _onKeydown, true);

        // bfcache: ``Cache-Control: no-store`` (Refs #1342) verhindert einen
        // Restore nicht garantiert in jedem Browser. Kommt die Seite trotzdem
        // aus dem Vor-/Zurueck-Cache, stuende dort der Inhalt VOR dem
        // Verlassen — also verdecken und neu laden; nach dem Logout endet der
        // Reload auf /login/. Nur auf angemeldeten Online-Seiten: in der
        // Offline-Shell wuerde ein Reload ins Netz laufen.
        if (_logoutUrl()) {
            window.addEventListener("pageshow", function (event) {
                if (!event.persisted) return;
                _showOverlay("exit");
                _shield(true);
                window.location.reload();
            });
        }

        if (config.privacyScreen) {
            window.addEventListener("blur", armPrivacyScreen);
            document.addEventListener("visibilitychange", function () {
                if (document.hidden) armPrivacyScreen();
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    // Nur fuer die Tests und den Offline-Pfad sichtbar; die UI ruft
    // ausschliesslich ueber Knopf und Tastenkuerzel.
    window.quickExit = {
        DEFAULT_URL: DEFAULT_URL,
        STORAGE_KEY: STORAGE_KEY,
        KEY_DROP_TIMEOUT_MS: KEY_DROP_TIMEOUT_MS,
        config: _config,
        trigger: quickExit,
        arm: armPrivacyScreen,
        release: releasePrivacyScreen,
    };
})();
