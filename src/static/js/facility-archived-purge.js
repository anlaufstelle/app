/*
 * Zustellung des Archivierungs-Purges ausserhalb der Bundle-Revalidierung
 * (Refs #2067, Cross-Review Finding 1 — Rest aus Fix-Runde 1).
 *
 * Fix-Runde 1 hat die Sperr-403 maschinenlesbar markiert
 * (``X-Facility-Archived: 1``) und ``revalidateCachedClient`` /
 * ``revalidateCachedFacility`` darauf purgen lassen. Damit war genau EIN
 * Zustellweg abgedeckt: eine laufende Bundle-Revalidierung. Normale
 * Navigationen und HTMX-Antworten reicht der Service Worker direkt vom Netz
 * weiter — dort wertete niemand den Marker aus, und der Zugriffsentzug haette
 * allein an ``Clear-Site-Data`` gehangen. Das Projekt dokumentiert selbst
 * (``docs/security-notes.md``), dass Safari diesen Header nur unvollstaendig
 * umsetzt; auf einem unterstuetzten Browser blieb der Pfad
 * "Navigation/HTMX -> Archiv-403 -> IndexedDB bleibt" also offen.
 *
 * Dieses Modul ist die gemeinsame Aufraeumroutine fuer beide verbleibenden
 * Wege:
 *
 *   1. ``htmx-errors.js`` ruft sie bei einer markierten 403 auf.
 *   2. Die Sperrseite ``auth/facility_archived.html`` laedt dieses Modul
 *      direkt; es startet dann per DOM-Marker von selbst.
 *
 * **Bewusst abhaengigkeitsfrei.** Die Sperrseite ist standalone (kein
 * ``base.html``) — Dexie, ``crypto.js`` und der Offline-Verbund sind dort
 * nicht geladen, und sie nur fuer eine Fehlerseite nachzuziehen waere teuer
 * und fragil. Der harte Weg ``indexedDB.deleteDatabase`` braucht nichts davon
 * und raeumt sogar gruendlicher ab als ``purgeAll`` (die Datenbank faellt
 * ganz, nicht nur ihr Inhalt).
 *
 * Ist der Verbund doch geladen (HTMX-Fall), laeuft ZUERST der bestehende,
 * getestete Wipe-Pfad: nur er verwirft zusaetzlich den CryptoKey im
 * Speicher der laufenden Seite. Erst danach fallen die Datenbanken selbst.
 */
(function () {
    "use strict";

    //: Beide IndexedDB-Datenbanken des Offline-Stacks — Bundle-Store und der
    //: separate Schluesselspeicher. Namen gespiegelt aus offline-db.js
    //: (``DB_NAME``) und crypto.js (``DB_NAME``).
    var DATABASES = ["anlaufstelle-offline", "anlaufstelle-crypto"];

    //: Harter Deckel wie beim Logout-Wipe (sw-register.js, Refs #1476): eine
    //: offene Verbindung laesst ``deleteDatabase`` auf ``blocked`` warten. Der
    //: Nutzer darf davon nicht haengen bleiben — die Tabellen sind zu diesem
    //: Zeitpunkt ueber ``purgeAll`` ohnehin schon leer.
    var DELETE_TIMEOUT_MS = 3000;

    function _deleteDatabase(name) {
        return new Promise(function (resolve) {
            var settled = false;
            function done() {
                if (!settled) {
                    settled = true;
                    resolve();
                }
            }
            window.setTimeout(done, DELETE_TIMEOUT_MS);
            try {
                var request = window.indexedDB.deleteDatabase(name);
                request.onsuccess = done;
                request.onerror = done;
                // ``onblocked`` NICHT aufloesen: der Delete laeuft weiter,
                // sobald die letzte Verbindung faellt. Der Timeout oben gibt
                // die Kontrolle trotzdem zurueck.
            } catch (_e) {
                done();
            }
        });
    }

    function _unregisterServiceWorkers() {
        // Der Zugriffsentzug beendet genau die Offline-Faehigkeit, die der SW
        // traegt — ihn stehen zu lassen hiesse, die App-Shell weiter aus dem
        // Cache zu bedienen. Betrifft ausschliesslich den ausdruecklich
        // markierten Archiv-Fall.
        try {
            if (!navigator.serviceWorker || !navigator.serviceWorker.getRegistrations) {
                return Promise.resolve();
            }
            return navigator.serviceWorker
                .getRegistrations()
                .then(function (registrations) {
                    return Promise.all(
                        (registrations || []).map(function (registration) {
                            return registration.unregister().catch(function () {});
                        })
                    );
                })
                .catch(function () {});
        } catch (_e) {
            return Promise.resolve();
        }
    }

    async function run() {
        // 1. Gemeinsamer Wipe-Pfad, falls der Verbund geladen ist — nur er
        //    raeumt zusaetzlich den CryptoKey im Speicher dieser Seite ab.
        try {
            if (window.crypto_session && window.crypto_session.wipeOfflineState) {
                await window.crypto_session.wipeOfflineState();
            } else if (window.offlineStore && window.offlineStore.purgeAll) {
                await window.offlineStore.purgeAll();
            }
        } catch (_e) {
            // ignore — der harte Weg unten raeumt ohnehin ab.
        }

        // 2. Datenbanken ganz entfernen. Auf der Sperrseite ist das der
        //    einzige Schritt, der ueberhaupt etwas tun kann.
        await Promise.all(DATABASES.map(_deleteDatabase));

        // 3. Service-Worker-Registrierungen loesen.
        await _unregisterServiceWorkers();
    }

    window.facilityArchivedPurge = { run: run, DATABASES: DATABASES.slice() };

    // Selbststart auf der Sperrseite. Der Marker sitzt im Markup statt in
    // einem Inline-Script, weil die CSP kein ``script-src 'unsafe-inline'``
    // erlaubt. Auf allen anderen Seiten bleibt das Modul inert und wird
    // ausschliesslich von htmx-errors.js aufgerufen.
    function _autostart() {
        if (document.querySelector("[data-facility-archived-purge]")) {
            run().catch(function () {});
        }
    }
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", _autostart);
    } else {
        _autostart();
    }
})();
