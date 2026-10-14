/*
 * Service Worker for Anlaufstelle PWA.
 *
 * - App-Shell cache for static assets (cache-first)
 * - Network-first for HTML/HTMX
 * - POST/PUT on whitelisted URLs gets queued via the document-side
 *   window.offlineQueue when the network fails
 * - Multipart-form-data POST is NOT queued offline (binary blobs require
 *   the encrypted IndexedDB pipeline planned in #574); we return 503 so the
 *   UI can show an explicit "Datei-Upload erfordert Internetverbindung"
 *   message instead of silently dropping the upload (#567).
 *
 * Refs #573, #576, #1351, #1386.
 */

importScripts("/static/js/url-patterns.js");

// Refs #1419: v17 -> v18 -- url-patterns.js (precached + importScripts)
// nimmt WORKITEM_STATUS in QUEUE_PATTERNS auf; der Bump erzwingt bei
// Bestandsnutzern Re-Install + Re-Precache, damit auch der laufende SW die
// neuen Queue-Regeln laedt.
// Refs #1482: v18 -> v19 -- APP_SHELL um die Shell-Renderer-Deps erweitert
// (alpine-csp.min.js, alpine/base-layout.js, sync-orchestrator.js); der Bump
// erzwingt Re-Install + Re-Precache, damit Bestandsnutzer die Module offline
// bekommen.
// Refs #1523 (#1499): v19 -> v20 -- APP_SHELL um die pk-losen
// Create-Shells (OFFLINE_EVENT_SHELL_URL/OFFLINE_WORKITEM_SHELL_URL) und ihre
// Renderer (offline-create.js + der reine Feld-Helfer offline-form-fields.js)
// erweitert; der Bump erzwingt Re-Install + Re-Precache, damit der
// Kalt-Offline-Pfad (/events/new/, /workitems/new/) den neuen Erfassungs-Shell
// findet statt der /offline/-Sackgasse.
// Refs #1533 (#1499): v20 -> v21 -- APP_SHELL um die pk-lose
// Personenlisten-Shell (OFFLINE_CLIENT_LIST_SHELL_URL = /offline/clients/) und
// ihren Renderer (offline-client-list.js) erweitert; der SW serviert die Liste
// offline IN-PLACE an der kanonischen URL /clients/ (neuer CLIENT_LIST-Zweig).
// Der Bump erzwingt Re-Install + Re-Precache, damit der Kalt-Offline-Pfad
// (/clients/) den neuen Listen-Shell findet statt der /offline/-Home.
// Refs #1543 (#1499): v21 -> v22 -- APP_SHELL um die Aufgaben-Shell
// (OFFLINE_WORKITEM_LIST_SHELL_URL = /offline/workitems/) und die Zeitstrom-
// Chronik-Shell (OFFLINE_ZEITSTROM_SHELL_URL = /offline/zeitstrom/) samt ihrer
// Renderer (offline-workitem-list.js/offline-zeitstrom.js) erweitert; der SW
// serviert sie offline IN-PLACE an den kanonischen URLs /workitems/ bzw. /
// (neue WORKITEM_LIST-/ZEITSTROM-Zweige). Der Bump erzwingt Re-Install +
// Re-Precache, damit der Kalt-Offline-Pfad die neuen Tab-Shells findet statt
// der /offline/-Home.
// Refs #1339: v23 -> v24 -- APP_SHELL um den globalen HTMX-Fokus-Handler
// (focus-management.js) erweitert. base.html laedt ihn auf jeder Seite (auch
// in den In-Place-Offline-Shells, die base.html erweitern); ohne Pre-Cache
// bliebe der Fokus-Handler im Kalt-Offline-Pfad nach einem SW-Update aus. Der
// Bump erzwingt Re-Install + Re-Precache.
// Refs #1546: v22 -> v23 -- SW-Cache-Hygiene / Storage-Budget. Der
// stale-while-revalidate-Zweig schrieb bisher JEDE erfolgreiche
// /static/-Antwort ungebremst in den Precache (CACHE_NAME) — gehashte
// Asset-URLs akkumulierten ueber Release-Generationen (~245 MB beobachtet).
// Ab jetzt landet die Laufzeit-Revalidierung in einem GETRENNTEN Runtime-Cache
// (RUNTIME_CACHE_NAME) mit Eintrags-Budget (MAX_RUNTIME_ENTRIES); der Precache
// bleibt unangetastet. Der Bump erzwingt Re-Install + Re-Activate und raeumt
// dabei die alten, ungebremst gewachsenen v22-Caches (Precache + Runtime) weg.
// Refs #1575: v24 -> v25 -- Kommentare in den Quellen der precachten Shells
// ueberarbeitet. Der Shell-Bump-Guard pinnt auf die Shell-QUELLE, nicht auf das
// gerenderte Ergebnis: jede Aenderung dort verlangt einen CACHE_NAME-Bump, sonst
// bekaemen Bestandsnutzer die neue Shell nie zu sehen (cache-first, keine
// Revalidierung). Nur den Pin zu aktualisieren wuerde genau die Luecke
// aufreissen, die der Guard schliesst.
// Refs #1493: v25 -> v26 -- crypto.js + sync-orchestrator.js (beide im
// APP_SHELL, cache-first ohne Revalidierung) tragen den reentranz-bewussten
// Boot-Idle-Gate. Ohne Bump bekaemen Bestandsnutzer die neue Serialisierung
// nie (der Precache liefert die alten Module weiter). Der Bump erzwingt
// Re-Install + Re-Precache.
// Refs #1602: v26 -> v27 -- base.html bindet im <head> den Umami-Tracker ein
// (partials/_umami.html, nur bei gesetzter UMAMI_WEBSITE_ID = devlive/demo).
// Die precachten In-Place-Offline-Shells erweitern base.html; ohne Bump saehen
// Bestandsnutzer die geaenderte Shell nie (cache-first ohne Revalidierung). Der
// Bump erzwingt Re-Install + Re-Precache.
// Refs #1604: v27 -> v28 -- base.html bindet zusaetzlich das Demo-Consent-Banner
// (partials/_umami_consent.html) im <body> ein und ergaenzt die Rechtstext-
// Footerlinks der Demo. Erneut base.html-Shell-Aenderung -> Bump erzwingt
// Re-Install + Re-Precache.
// Refs #1476 (R10): v28 -> v29 -- conflict_list.html (precachte Shell) traegt
// jetzt den Offline-Notiz-Export-Hinweis (data-confirm-export/data-label-
// exported). Shell-Aenderung -> Bump erzwingt Re-Install + Re-Precache.
// Refs #1660 (I5 aus #1376): v29 -> v30 -- die style-src-Haertung aendert
// styles.css (htmx-Indicator-Regeln + VVT-Print-Regeln wandern aus geloeschten
// Inline-<style>-Bloecken ins Stylesheet) und base.html (htmx-config-Meta).
// styles.css liegt UNGEHASHT im Precache: ohne Bump bekaeme ein bestehender
// Client die neuen Regeln offline nie zu sehen und die Lade-Spinner blieben
// dauerhaft eingeblendet. Bump erzwingt Re-Install + Re-Precache.
// Refs #1738 (M24/AP5): v30 -> v31 -- url-patterns.js (precached +
// importScripts) erweitert ATTACHMENT_DOWNLOAD auf die owner-generischen
// Routen (/clients/<uuid>/attachments/<uuid>/download/ und die Fall-Variante).
// Genau wie beim v17 -> v18-Bump gilt: ein laufender SW haelt seine Kopie des
// Moduls aus dem Precache (cache-first, keine Revalidierung) und wuerde die
// neuen Downloads weiter in die Navigations-Fallback-Kette schicken — der
// Benutzer saehe offline eine Ersatzseite statt eines Fehlers. Der Bump
// erzwingt Re-Install + Re-Precache.
// Refs #1774: v31 -> v32 -- offline-store.js (im APP_SHELL, cache-first ohne
// Revalidierung) traegt die erweiterte Reichweite des Offline-Widerrufs
// (Ereignis->Person-Aufloesung fuer gequeute Edit-POSTs). Ohne Bump behielten
// Bestandsnutzer ihre alte Kopie aus dem Precache — der Widerruf liesse den
// gequeuten POST weiter stehen und der Replay schriebe nach der Loeschung.
// Genau wie beim v25 -> v26-Bump (crypto.js/sync-orchestrator.js) erzwingt der
// Bump Re-Install + Re-Precache und raeumt zugleich den SWR-Runtime-Cache
// (autosave.js liegt dort) weg.
// Refs #1912: v34 -> v35 -- url-patterns.js nimmt den exakt geankerten Tally-
// POST auf; offline-store.js wechselt das Facility-Bundle auf Schema v2 und
// offline-tally.js rendert cold/warm Pending-Zustaende. Alle drei liegen im
// cache-first APP_SHELL und muessen atomar erneuert werden.
// Refs #1937: v35 -> v36 -- offline-tally.js und die kalte Zeitstrom-Shell
// zeigen den bestaetigten Serverstand wieder getrennt von Pending-Deltas und
// einen absoluten Snapshot-Zeitpunkt. Beide liegen cache-first im APP_SHELL;
// ohne Bump bliebe Bestandsnutzern der alte, irrefuehrende Tally-Status.
// Refs #1966: v37 -> v38 -- alle acht precachten Shells erben jetzt das
// statische offline_base.html statt base.html. Der Precache-Fetch laeuft
// same-origin und damit MIT Cookies: ein Install/Update waehrend einer
// angemeldeten Sitzung schrieb bisher die personalisierte Chrome-Variante
// (Klarname, Rolle, Organisation/Einrichtung, data-user-id, data-session-age,
// CSRF-Token) in den Cache, wo sie cache-first ohne Revalidierung ausgeliefert
// wird und Sitzungsende, Sperrung und Geraetewechsel ueberlebt. Ohne Bump
// behielten genau die betroffenen Bestandsgeraete ihre personalisierte Kopie —
// der Bump ist hier also nicht Hygiene, sondern der Loeschvorgang: er erzwingt
// Re-Install + Re-Precache und raeumt im activate die v37-Generation samt
// Runtime-Cache weg.
// Refs #1838: v40 -> v41 -- offline-store.js ist in den 4-Datei-Verbund
// offline-db/offline-bundles/offline-revocations/offline-store gesplittet:
// drei NEUE precachte Dateien, ein deutlich verkleinertes offline-store.js
// und fuenf geaenderte (teils precachte) Shell-Templates mit den neuen
// Script-Tags. Alles cache-first im APP_SHELL: ohne Bump behielte ein
// Bestandsgeraet das alte Monolith-offline-store.js OHNE die drei neuen
// Dateien im Precache — je nach Shell-Stand fehlte die halbe Fassade. Der
// Bump erzwingt Re-Install + Re-Precache und erneuert den Verbund atomar.
// Refs #1947: v38 -> v39 -- Manifest, Meta-Tags und PWA-Icons verwenden jetzt
// dieselbe Markenfarbe; der Splash-Hintergrund entspricht dem warmen Canvas.
// base.html/offline_base.html sowie offline-tally.js liegen in precachten
// Shells. Der Bump ersetzt die alten Farben und 36-px-Touchziele auch auf
// bereits installierten Geraeten.
// Refs #1828: v39 -> v40 -- Quick-Win-Welle Offline-JS. Praktisch alle
// precachten Offline-Module aendern sich zusammen: url-patterns.js traegt den
// neuen ENDPOINTS-Block, crypto.js das awaitReady-Gate, offline-store/
// offline-queue/offline-edit/offline-client/sync-orchestrator konsumieren
// beides, die frueheren Spiegel-Helfer und Formatter leben jetzt in
// offline-form-fields.js, und sechs precachte Shell-Templates laden diese
// Datei neu per Script-Tag. Alles liegt cache-first im APP_SHELL: ohne Bump
// behielte ein Bestandsgeraet eine Mischung aus alten und neuen Modulen —
// z.B. ein neues offline-client-view.js, das offlineFormFields.formatTs
// erwartet, gegen ein altes offline-form-fields.js ohne Formatter. Der Bump
// erzwingt Re-Install + Re-Precache und erneuert die Module atomar.
// Refs #2043: v42 -> v43 -- die precachte Personenlisten-Shell bindet ueber
// components/_contact_stage_legend.html die neue Kontexthilfe ein. Der
// Online-Link zur angemeldeten Hilfeseite bleibt in dieser Offline-Shell
// bewusst aus; das geaenderte Shell-Markup muss Bestandsgeraete trotzdem per
// Re-Install + Re-Precache erreichen.
// Refs #1059: v44 -> v45 -- offline.html traegt jetzt Skip-Link und main-Ziel;
// rekursiv eingebundene Offline-Formular-Partials liefern eindeutige
// Label-/Feld-Beziehungen und Live-Regionen. Der Bump stellt das geaenderte
// gerenderte Shell-Markup auf Bestandsgeraeten atomar bereit.
// Refs #2136: v45 -> v46 -- der Mitschauen-Schutz bringt eine NEUE precachte
// Datei (quick-exit.js) und geaendertes Shell-Markup mit (Knopf, Sichtblende,
// gemeinsamer Sticky-Wrapper in offline_base.html). Ohne Bump behielte ein
// Bestandsgeraet die alte Shell OHNE Knopf und ohne das Skript im Precache —
// die Aktion "Schnell verlassen" waere offline genau dort tot, wo sie
// gebraucht wird. Der Bump erzwingt Re-Install + Re-Precache.
// Refs #2201: v46 -> v47, damit Bestandsgeraete die gehaertete Shell laden.
// 2026-09-21, Refs #2238: v47 -> v48 -- offline-messages.js ist ein neues
// Shell-Modul; der Bump erneuert die atomare Precache-Generation.
// 2026-09-22, Refs #2293/#2289: v48 -> v49 — die precachten Replay-Skripte
// (offline-edit.js, offline-queue.js) tragen die korrigierte Metadaten-
// Uebernahme der Neuanlage und den Vertrag der Erfolgsbestaetigung; die
// precachte Shell bekommt den Hinweis „Anmeldung erforderlich".
// 2026-09-23, Refs #2352: v50 -> v51 -- das precachte quick-exit.js legt beim
// Schnell-Verlassen die alte Seite still (HTMX-Abbruch, Logout-Marke), damit
// keine spaete Antwort die Navigation zur neutralen Seite ueberschreibt. Der
// Bump bringt das geaenderte Modul auf Bestandsgeraete.
// 2026-09-24, Refs #2383: v52 -> v53 -- die precachte Offline-Personenansicht
// (offline_detail.html) hebt drei Textknoepfe auf 44px Mindesthoehe an. Der
// Bump bringt das geaenderte Markup auf Bestandsgeraete.
// 2026-09-29, Refs #1904: v54 -> v55 -- the delivered Content-Security-Policy
// gains base-uri/form-action. Cached responses keep the headers they were
// stored with, and activate keeps both caches of an unchanged generation, so
// only a new generation drops every precache and runtime entry stored with the
// earlier policy. A policy change always needs a bump (guard:
// TestPrecachedShellBumpGuard::test_policy_change_requires_cache_name_bump).
// 2026-10-07, Refs #2426: v56 -> v57 -- the precached crypto.js changes; the
// bump brings the changed module to installed devices.
// Refs #1493: v62 -> v63 -- the precached crypto.js no longer lets a lock
// holder wait for the boot idle gate queued behind it; the bump brings the
// changed module to installed devices.
const CACHE_NAME = "anlaufstelle-v64";
// Refs #1546: Getrennter Laufzeit-Cache fuer den stale-while-revalidate-Zweig,
// vom Namen des Precache abgeleitet — so benennt ein CACHE_NAME-Bump
// automatisch auch den Runtime-Cache neu (alte Generation faellt im activate
// weg). Der Precache (CACHE_NAME) enthaelt ausschliesslich das kuratierte
// APP_SHELL und wird NIE getrimmt.
const RUNTIME_CACHE_NAME = CACHE_NAME + "-runtime";
// Refs #1546: Eintrags-Budget fuer den Runtime-Cache. Bewusst ein Eintrags-Cap
// (nicht Byte-basiert): cache.keys() liefert die Eintraege in
// Insertion-Reihenfolge, sodass sich die aeltesten ohne jeglichen In-Memory-
// Zustand (der SW-Restarts nicht ueberlebte) zuerst verdraengen lassen. 150
// deckt App-Shell-Revalidierungen + uebliche Runtime-Assets grosszuegig ab.
const MAX_RUNTIME_ENTRIES = 150;
// Refs #701: dediziertes Fallback-Template fuer Navigation-Requests
// ohne Cache- und Netz-Hit. Wird als App-Shell pre-cached, damit es
// auch beim ersten Offline-Aufruf garantiert verfuegbar ist.
const OFFLINE_FALLBACK_URL = "/offline/";
// Refs #1322: generischer, pk-loser Shell fuer In-Place-Rendern an der
// kanonischen URL /clients/<pk>/ (offline) — statt Redirect auf /offline/...
const OFFLINE_CLIENT_SHELL_URL = "/offline/client-shell/";
// Refs #1396: generischer, pk-loser Shell fuer In-Place-Rendern an der
// kanonischen URL /offline/conflicts/<pk>/ (offline) — statt Bounce auf
// /offline/. Muster wie OFFLINE_CLIENT_SHELL_URL.
const OFFLINE_CONFLICT_SHELL_URL = "/offline/conflict-shell/";
// Refs #1523 (#1499): generische, pk-lose Create-Shells fuer das
// In-Place-Rendern an den kanonischen URLs /events/new/ bzw. /workitems/new/
// (offline) — statt Bounce auf /offline/. Muster wie OFFLINE_CLIENT_SHELL_URL.
const OFFLINE_EVENT_SHELL_URL = "/offline/event-shell/";
const OFFLINE_WORKITEM_SHELL_URL = "/offline/workitem-shell/";
// Refs #1533 (#1499): generische, pk-lose Personenlisten-Shell fuer das
// In-Place-Rendern an der kanonischen URL /clients/ (offline) — statt Bounce
// auf /offline/. Muster wie OFFLINE_CLIENT_SHELL_URL; die Shell selbst ist PII-
// frei, offline-client-list.js fuellt sie aus der verschluesselten IndexedDB.
const OFFLINE_CLIENT_LIST_SHELL_URL = "/offline/clients/";
// Refs #1543 (#1499): generische, pk-lose Aufgaben-Listen-Shell fuer
// das In-Place-Rendern an der kanonischen URL /workitems/ (offline) — statt
// Bounce auf /offline/. PII-frei; offline-workitem-list.js fuellt sie aus der
// verschluesselten IndexedDB (listOfflineWorkItemsAggregated, cross-client +
// standalone). Muster wie OFFLINE_CLIENT_LIST_SHELL_URL.
const OFFLINE_WORKITEM_LIST_SHELL_URL = "/offline/workitems/";
// Refs #1543 (#1499): generische, pk-lose Zeitstrom-Chronik-Shell fuer
// das In-Place-Rendern an der Wurzel-URL / (offline). PII-frei; offline-
// zeitstrom.js fuellt sie aus der verschluesselten IndexedDB
// (listOfflineEventsAggregated, chronologisch inkl. anonymer Eintraege).
const OFFLINE_ZEITSTROM_SHELL_URL = "/offline/zeitstrom/";
// Refs #1386: Timeouts gegen Lie-Fi (Verbindung meldet sich als "online",
// haengt aber ohne Antwort/Fehler). Ohne Timeout haengt respondWith()
// endlos, statt in die vorhandenen Queue-/Offline-Fallback-Ketten zu laufen.
const WRITE_FETCH_TIMEOUT_MS = 6000;
const READ_FETCH_TIMEOUT_MS = 8000;
// Refs #1412 (M17): user-sichtbare SW-Strings. Der SW hat keinen Django-
// Template-Kontext; GET /sw.js ersetzt den markierten Block unten pro Request
// durch die gettext-uebersetzten Strings der Request-Sprache
// (ServiceWorkerView._inject_sw_i18n). Der Default hier ist Deutsch (Praezedenz
// + Dev-Lesbarkeit). {reason} ist ein Platzhalter, den der fetch-Handler
// client-seitig via .replace() ersetzt. Die DE-Quellstrings sind zugleich die
// gettext-msgids (aus core/views/pwa.py extrahiert).
// __SW_I18N_START__
const SW_I18N = {
    uploadOffline:
        "Offline — Datei-Uploads erfordern eine Internetverbindung. " +
        "Bitte erneut versuchen, sobald Sie online sind.",
    queuePersistFailed:
        "Offline — Ihre Eingaben konnten nicht lokal gespeichert werden ({reason}). " +
        "Bitte erneut versuchen, sobald Sie online sind.",
    queuedOk:
        "Offline — Ihre Eingaben wurden lokal verschlüsselt und werden bei Verbindung automatisch gesendet.",
    partialBanner: "Offline — dieser Inhalt kann gerade nicht aktualisiert werden.",
};
// __SW_I18N_END__
// Refs #1413: Die /static/-Eintraege dieses APP_SHELL werden beim Ausliefern von
// GET /sw.js serverseitig durch staticfiles_storage.url() aufgeloest
// (ServiceWorkerView._resolve_app_shell). In Produktion (Manifest-Storage)
// werden daraus die GEHASHTEN URLs — exakt die, die {% static %} in den
// Templates referenziert. Nur so trifft caches.match() die pre-cachten Assets.
// Die Quelldatei bleibt bewusst dev-lesbar mit ungehashten Pfaden; in Dev/Test
// (kein Manifest / DEBUG) liefert die Aufloesung dieselben Pfade zurueck.
// Refs #2556: Public shells have explicit, account-neutral language variants.
const SHELL_URLS = [
    OFFLINE_FALLBACK_URL, OFFLINE_CLIENT_SHELL_URL, OFFLINE_CONFLICT_SHELL_URL,
    OFFLINE_EVENT_SHELL_URL, OFFLINE_WORKITEM_SHELL_URL, OFFLINE_CLIENT_LIST_SHELL_URL,
    OFFLINE_WORKITEM_LIST_SHELL_URL, OFFLINE_ZEITSTROM_SHELL_URL, "/offline/conflicts/",
];
const SHELL_LANGUAGE_KEY = "/offline/shell-language/";
let shellLanguageUpdate = Promise.resolve();

const APP_SHELL = [
    ...SHELL_URLS.flatMap(url => ["de", "en"].map(language => `${url}?shell_language=${language}`)),
    "/static/css/styles.css",
    "/static/icons/icon-192.png",
    "/static/icons/icon-512.png",
    "/static/icons/icon-192.svg",
    "/static/icons/icon-512.svg",
    // Refs #1321: Der Offline-Arbeitsplatz (/offline/) rendert seine
    // Personenliste client-seitig aus der verschluesselten IndexedDB. Seine
    // JS-Deps muessen pre-cached sein, sonst ist die Home beim ersten
    // Offline-Aufruf (PWA-Kaltstart) leer/unladbar.
    "/static/js/dexie.min.js",
    "/static/js/crypto.js",
    // Refs #1838: offline-store ist ein 4-Datei-Verbund (offline-db →
    // offline-bundles → offline-revocations → offline-store, EINE Fassade);
    // alle vier muessen atomar precached sein — eine Mischgeneration
    // (z.B. neues offline-store.js ohne offline-db.js) haette weder Fassade
    // noch internen Kanal.
    "/static/js/offline-db.js",
    "/static/js/offline-bundles.js",
    "/static/js/offline-revocations.js",
    "/static/js/offline-store.js",
    "/static/js/offline-home.js",
    // Refs #1322: Der In-Place-Client-Shell rendert ueber offline-client-view.js,
    // das NUR vom Offline-Detail-Template geladen wird (sonst nirgends -> nicht
    // per stale-while-revalidate gecacht). Ohne Pre-Cache bliebe der Shell
    // offline ohne Renderer.
    "/static/js/offline-client-view.js",
    // Refs #1523 (#1499): Die pk-losen Create-Shells (offline_create.html
    // fuer Event/WorkItem) rendern ueber offline-create.js; der reine
    // Feld-Helfer offline-form-fields.js speist den Event-Feld-Loop.
    // Beide werden NUR von den Create-/Detail-Templates geladen (sonst nirgends
    // -> nicht per stale-while-revalidate gecacht). Ohne Pre-Cache bliebe der
    // Kalt-Offline-Pfad (/events/new/, /workitems/new/) nach einem SW-Update
    // ohne Renderer.
    "/static/js/offline-form-fields.js",
    // Refs #2603: base.html and offline_base.html bind date fields to the app
    // language through date-input-i18n.js; without precaching, an offline cold
    // start after a SW update shows browser-locale date fields.
    "/static/js/date-input-i18n.js",
    "/static/js/offline-messages.js",
    "/static/js/offline-create.js",
    // Refs #1533 (#1499): Die pk-lose Personenlisten-Shell (offline_list.html)
    // rendert ueber offline-client-list.js; das Modul wird NUR vom Listen-Template
    // geladen (sonst nirgends -> nicht per stale-while-revalidate gecacht). Ohne
    // Pre-Cache bliebe der Kalt-Offline-Pfad (/clients/) nach einem SW-Update ohne
    // Renderer.
    "/static/js/offline-client-list.js",
    // Refs #1543 (#1499): Die pk-losen Tab-Shells (offline_workitem_list.html
    // /offline_zeitstrom.html) rendern ueber offline-workitem-list.js bzw.
    // offline-zeitstrom.js; die Module werden NUR von ihren Templates geladen
    // (sonst nirgends -> nicht per stale-while-revalidate gecacht). Ohne
    // Pre-Cache bliebe der Kalt-Offline-Pfad (/workitems/, /) nach einem
    // SW-Update ohne Renderer.
    "/static/js/offline-workitem-list.js",
    "/static/js/offline-zeitstrom.js",
    // #1912: gemeinsamer Renderer fuer die kalte Tally-Shell und die
    // progressive Pending-/Blocked-Anzeige im warmen Online-Zeitstrom.
    "/static/js/offline-tally.js",
    // Refs #1396: Die Konflikt-/Dead-Letter-Verwaltung (/offline/conflicts/)
    // und der pk-lose Review-Shell rendern client-seitig aus der
    // verschluesselten IndexedDB. Ihre Renderer werden NUR von den
    // Konflikt-Templates geladen (sonst nirgends -> nicht per
    // stale-while-revalidate gecacht) — ohne Pre-Cache blieben Liste/Shell
    // offline ohne Renderer. Die Liste selbst wird pre-cached, damit ein
    // Badge-Klick offline die generische caches.match(request)-Fallback-Stufe
    // trifft (kein eigener sw.js-Zweig noetig); der pk-behaftete Review greift
    // ueber extractConflictPk den Shell IN-PLACE.
    "/static/js/conflict-list.js",
    "/static/js/conflict-resolver.js",
    // Refs #1386: Diese Module treiben den Offline-Sync-Kern (CSRF-Refresh,
    // URL-Whitelist, Queue, "Offline mitnehmen"-Client-Cache, Event-Edit-
    // Replay) und werden auch von Seiten geladen, die selbst NICHT im
    // APP_SHELL stehen (z.B. Client-Liste/-Detail) — ohne Pre-Cache waeren
    // sie beim ersten Offline-Aufruf einer noch nicht besuchten Seite nicht
    // ladbar.
    "/static/js/csrf.js",
    // Refs #1408: gemeinsames CSRF-Util (fromMeta/refresh) der Replay-
    // Konsumenten — muss wie sie pre-cached sein, sonst laeuft der Offline-
    // Replay-Refresh-Pfad ins Leere (window.csrfUtils fehlt).
    "/static/js/csrf-utils.js",
    "/static/js/url-patterns.js",
    "/static/js/offline-queue.js",
    "/static/js/offline-client.js",
    "/static/js/offline-edit.js",
    // Refs #1482: Die In-Place-Shells (offline_detail.html/conflict_review.html)
    // erweitern seit Refs #1966 offline_base.html — ihr gesamtes Rendering
    // haengt an Alpine (alpine:init in offline-client-view.js/
    // conflict-resolver.js; loading/unavailable/Inhalt stehen in
    // <template x-if>), der Offline-Statusbalken an der Layout-Komponente
    // offlineStatus. Die Navigation der Shells ist dagegen statisch und
    // braucht kein JS mehr (die rollenabhaengige Bottom-Nav mit
    // createMenu/mobileMore steht nur noch auf base.html-Seiten). Der
    // Sync-Orchestrator ist der einzige koordinierte Replay-Trigger
    // (ADR-030); ohne ihn fallen queue/edit/store auf ihre unkoordinierten
    // online-Listener zurueck (Pre-M6-Race). Nach einem CACHE_NAME-Bump ist
    // der SWR-Runtime-Cache leer — ohne Pre-Cache bleibt ein
    // Offline-Kaltstart ohne Renderer/Statusbalken/Koordinator.
    "/static/js/alpine-csp.min.js",
    "/static/js/alpine/base-layout.js",
    "/static/js/sync-orchestrator.js",
    // Refs #1339: Der globale HTMX-Fokus-Handler (htmx:afterSwap) wird von
    // base.html auf jeder Seite geladen — auch von den In-Place-Offline-Shells
    // (offline_detail.html etc.), die base.html erweitern und selbst hx-Swaps
    // rendern. Ohne Pre-Cache bliebe der Fokus-Handler im Kalt-Offline-Pfad nach
    // einem SW-Update (leerer SWR-Runtime-Cache) aus.
    "/static/js/focus-management.js",
    // Refs #2136 (N-2): Der Mitschauen-Schutz ist offline genauso noetig wie
    // online — die Shell traegt denselben Knopf und dieselbe Sichtblende. Ohne
    // Pre-Cache bliebe das Markup im Kalt-Offline-Pfad ohne Verhalten (der
    // Knopf taete nichts, das Tastenkuerzel gaebe es nicht).
    "/static/js/quick-exit.js",
    // Refs #1334: Das PWA-Manifest (aus Scope-Gruenden unter /manifest.json,
    // nicht /static/) und das deklarierte Site-Icon (favicon.svg) offline
    // verfuegbar machen — sonst net::ERR_INTERNET_DISCONNECTED beim Offline-
    // Laden. Das Manifest faellt nicht in den /static/-SWR-Zweig und bekommt
    // deshalb unten (fetch-Handler) einen eigenen Zweig.
    "/manifest.json",
    "/static/icons/favicon.svg",
    OFFLINE_FALLBACK_URL,
    OFFLINE_CLIENT_SHELL_URL,
    OFFLINE_CONFLICT_SHELL_URL,
    // Refs #1523 (#1499): pk-lose Create-Shells — offline IN-PLACE an
    // /events/new/ bzw. /workitems/new/ serviert (Fallback-Zweige unten).
    OFFLINE_EVENT_SHELL_URL,
    OFFLINE_WORKITEM_SHELL_URL,
    // Refs #1533 (#1499): pk-lose Personenlisten-Shell — offline IN-PLACE
    // an /clients/ serviert (CLIENT_LIST-Fallback-Zweig unten).
    OFFLINE_CLIENT_LIST_SHELL_URL,
    // Refs #1543 (#1499): pk-lose Tab-Shells — offline IN-PLACE an
    // /workitems/ bzw. / serviert (WORKITEM_LIST-/ZEITSTROM-Fallback-Zweige unten).
    OFFLINE_WORKITEM_LIST_SHELL_URL,
    OFFLINE_ZEITSTROM_SHELL_URL,
    // Refs #1396: pk-lose Konflikt-Liste — public + datenlos, wird offline
    // ueber die generische caches.match(request)-Fallback-Stufe serviert.
    "/offline/conflicts/",
];

self.addEventListener("install", (event) => {
    event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
});

// Refs #1948: Precache-Selbstheilung. Raeumt der Browser unter Speicherdruck
// die Cache Storage teilweise ab (auf iOS realistisch), laufen weder install
// noch activate je wieder — das Geraet verloere still seine
// Offline-Faehigkeit bis zum naechsten CACHE_NAME-Bump. sw-register.js
// schickt deshalb einmal pro Seitenladen CHECK_PRECACHE; hier wird jeder
// APP_SHELL-Eintrag per URL-genauem cache.match geprueft (der String-Match
// umgeht den Vary-Mismatch der HTML-Shells, siehe den Fallback-Zweig unten)
// und NUR das Fehlende nachgeladen. Vollstaendiger Cache => reiner
// Match-Sweep ohne Netzzugriff. Fehler (z.B. offline waehrend der Heilung)
// werden geschluckt — die naechste Navigation stoesst den Check erneut an.
async function _healPrecache(language) {
    try {
        const cache = await caches.open(CACHE_NAME);
        if (language) await cache.put(SHELL_LANGUAGE_KEY, new Response(language));
        const missing = [];
        for (const url of APP_SHELL) {
            if (!(await cache.match(url))) {
                missing.push(url);
            }
        }
        if (missing.length > 0) {
            await cache.addAll(missing);
        }
    } catch (_e) {
        // still — Heilung ist best-effort, naechster Seitenaufruf probiert es
        // erneut. Ein Teil-Erfolg ist ausgeschlossen (addAll ist atomar).
    }
}

// Refs #1386: Update-Gate. Ein frisch installierter SW bleibt in
// "waiting", bis die Seite explizit SKIP_WAITING schickt (Klick auf
// "Neu laden" im Update-Toast, siehe sw-register.js). Ohne dieses Gate
// uebernahm der neue SW sofort (skipWaiting() ungegated im
// install-Handler) — der Toast suggerierte eine Kontrolle, die er nicht
// hatte.
self.addEventListener("message", (event) => {
    if (event.data?.type === "SET_SHELL_LANGUAGE" && ["de", "en"].includes(event.data.language)) {
        shellLanguageUpdate = shellLanguageUpdate.catch(() => {}).then(() => _healPrecache(event.data.language));
        event.waitUntil(shellLanguageUpdate);
    }
    if (event.data?.type === "SKIP_WAITING") {
        self.skipWaiting();
    }
    // Refs #1948: siehe _healPrecache oben. waitUntil haelt den SW waehrend
    // des Match-Sweeps/Nachladens am Leben (er wird zwischen Events entladen).
    if (event.data?.type === "CHECK_PRECACHE") {
        event.waitUntil(_healPrecache());
    }
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        // Refs #1546: Sowohl den aktuellen Precache (CACHE_NAME) als auch den
        // zugehoerigen Runtime-Cache (RUNTIME_CACHE_NAME) behalten; alle
        // uebrigen Caches — auch alte Precache- UND alte -runtime-Generationen
        // frueherer Versionen — loeschen. So verschwindet der ungebremst
        // gewachsene v22-Cache beim Aktivieren des v23-SW.
        caches.keys().then((keys) =>
            Promise.all(
                keys
                    .filter((k) => k !== CACHE_NAME && k !== RUNTIME_CACHE_NAME)
                    .map((k) => caches.delete(k))
            )
        )
    );
    self.clients.claim();
});

// Refs #1546: Runtime-Cache nach jedem Schreiben auf das Eintrags-Budget
// kappen. cache.keys() liefert die Requests in Insertion-Reihenfolge — die
// aeltesten Eintraege stehen vorn und werden zuerst verdraengt, bis
// MAX_RUNTIME_ENTRIES wieder eingehalten ist. Bewusst KEIN echtes LRU mit
// Zugriffs-Tracking: dessen Zustand muesste In-Memory oder in IndexedDB liegen
// und wuerde SW-Restarts nicht ueberleben (der SW wird zwischen Events
// entladen). Nur der Runtime-Cache wird angefasst — der Precache (CACHE_NAME)
// bleibt garantiert unberuehrt.
async function _trimRuntimeCache() {
    const cache = await caches.open(RUNTIME_CACHE_NAME);
    const keys = await cache.keys();
    const overflow = keys.length - MAX_RUNTIME_ENTRIES;
    for (let i = 0; i < overflow; i++) {
        await cache.delete(keys[i]);
    }
}

function shouldQueueRequest(request) {
    return self.URL_PATTERNS.isQueueableWriteRequest(request.url, request.method);
}

function isMultipart(request) {
    const ct = request.headers.get("content-type") || "";
    return ct.toLowerCase().startsWith("multipart/form-data");
}

const IDEMPOTENCY_KEY_RE = /^[A-Za-z0-9_-]{1,64}$/;

// #1912: No-JS-Tally traegt den stabilen Key als URL-encoded Body-Fallback.
// Fehlt nur der Header, muss dieser Body-Key bereits den ERSTVERSUCH und spaeter
// den Queue-Replay verbinden; ein neu geminteter Header waere ein vom SW selbst
// erzeugter Header/Body-Widerspruch und der Server lehnt korrekt mit 400 ab.
function tallyBodyIdempotencyKey(request, body) {
    const patterns = self.URL_PATTERNS;
    const contentType = request.headers.get("content-type") || "";
    if (!contentType.toLowerCase().startsWith("application/x-www-form-urlencoded")) return null;
    if (!patterns.TALLY_INCREMENT.test(new URL(request.url).pathname)) return null;
    const params = new URLSearchParams(body);
    const candidate = params.get("idempotency_key");
    return candidate && IDEMPOTENCY_KEY_RE.test(candidate) ? candidate : null;
}

// Refs #1351 (Bug #1): Request-Header sind immutabel — einen neuen Request aus
// dem Clone mit ergaenztem Header bauen (Body/Method/Credentials bleiben aus
// dem Input erhalten). Genutzt, um den SW-generierten Idempotency-Key an den
// Erstversuch-fetch zu haengen.
function _withHeader(request, name, value) {
    const headers = new Headers(request.headers);
    headers.set(name, value);
    return new Request(request.clone(), { headers: headers });
}

// Refs #1904: responses this worker builds itself (offline HTMX banner, flash
// fragments of the write path, the empty 503 of an uncached asset) never pass
// through Django and therefore get no Content-Security-Policy from the
// server's middleware chain. They adopt the policy of the precached offline
// home instead — a response the server built from its one policy source,
// refreshed with every precache generation — so this script keeps no copy of
// the policy. Without that source in the cache
// (evicted, lookup failed, header missing) the response falls back to a strict
// minimal policy, never to none. base-uri and form-action are listed
// explicitly because they do not fall back to default-src.
const POLICY_HEADER = "Content-Security-Policy";
const REPORT_ONLY_POLICY_HEADER = "Content-Security-Policy-Report-Only";
const FALLBACK_POLICY = "default-src 'none'; base-uri 'none'; form-action 'none'";

async function _withServerPolicy(response) {
    let source;
    try {
        source = await caches.match(OFFLINE_FALLBACK_URL);
    } catch (_e) {
        source = undefined;
    }
    const policy = source ? source.headers.get(POLICY_HEADER) : null;
    response.headers.set(POLICY_HEADER, policy || FALLBACK_POLICY);
    const reportOnly = policy ? source.headers.get(REPORT_ONLY_POLICY_HEADER) : null;
    if (reportOnly) {
        response.headers.set(REPORT_ONLY_POLICY_HEADER, reportOnly);
    }
    return response;
}

// Refs #1828: gemeinsame Flash-Response des Schreibpfads (vorher dreimal
// dupliziert). Rendert das #flash-messages-Fragment, das HTMX via
// HX-Retarget/HX-Reswap an die Standard-Flash-Stelle swappt. ``tone`` waehlt
// die Farbfamilie (red = Fehler, yellow = Offline-Hinweis); ``headers``
// ergaenzt die HTMX-Header (z.B. X-Offline-Queued).
function _flashResponse(text, opts) {
    const tone =
        opts.tone === "yellow"
            ? { box: "bg-yellow-50", text: "text-yellow-800" }
            : { box: "bg-red-50", text: "text-red-800" };
    const headers = Object.assign(
        {
            "Content-Type": "text/html; charset=utf-8",
            "HX-Retarget": "#flash-messages",
            "HX-Reswap": "outerHTML",
        },
        opts.headers || {}
    );
    // Refs #1904: resolves to the response carrying the server policy.
    return _withServerPolicy(
        new Response(
            '<div id="flash-messages">' +
                '<div class="rounded-md ' +
                tone.box +
                ' p-4 mb-4">' +
                '<p class="text-sm ' +
                tone.text +
                '">' +
                text +
                "</p></div></div>",
            { status: opts.status, statusText: opts.statusText, headers: headers }
        )
    );
}

// Refs #1828: Die Offline-Fallback-Kette fuer Navigationen als geordnete
// Tabelle — der erste Treffer gewinnt, die Array-Reihenfolge IST die Semantik
// der frueheren if-Kaskade (u.a. WORKITEM_NEW vor WORKITEM_LIST; die
// Listen-Regexes matchen die /new/-Routen dank exaktem (?:$|\?)-Terminator
// zwar ohnehin nicht, die Reihenfolge dokumentiert die Absicht). Jeder
// Eintrag serviert seinen pk-losen Shell IN-PLACE an der kanonischen URL
// (200, KEIN Redirect); die Renderer lesen ihren Kontext aus
// location.pathname + verschluesselter IndexedDB. Die matches-Callbacks
// greifen erst zur Aufrufzeit auf self.URL_PATTERNS zu (importScripts laeuft
// davor).
const SHELL_ROUTES = [
    // Refs #1322: Klientel-Detail (/clients/<pk>/) — offline-client-view.js
    // liest die pk aus location.pathname und rendert aus IndexedDB.
    {
        matches: (url) =>
            Boolean(self.URL_PATTERNS.extractClientPk && self.URL_PATTERNS.extractClientPk(url)),
        shell: OFFLINE_CLIENT_SHELL_URL,
    },
    // Refs #1396: Konflikt-Review (/offline/conflicts/<pk>/) — die pk-lose
    // Liste (/offline/conflicts/) matcht hier bewusst NICHT und faellt in die
    // generische caches.match(request.url)-Stufe (sie ist selbst pre-cached).
    {
        matches: (url) =>
            Boolean(self.URL_PATTERNS.extractConflictPk && self.URL_PATTERNS.extractConflictPk(url)),
        shell: OFFLINE_CONFLICT_SHELL_URL,
    },
    // Refs #1523 (#1499): Create-Shells — VOR den Listen-/generischen Stufen,
    // die sonst die /offline/-Home statt der Erfassungs-Shell liefern wuerden.
    // offline-create.js liest Katalog + mitgenommene Personen aus IndexedDB.
    {
        matches: (url) => self.URL_PATTERNS.matchesPath(self.URL_PATTERNS.EVENT_NEW, url),
        shell: OFFLINE_EVENT_SHELL_URL,
    },
    {
        matches: (url) => self.URL_PATTERNS.matchesPath(self.URL_PATTERNS.WORKITEM_NEW, url),
        shell: OFFLINE_WORKITEM_SHELL_URL,
    },
    // Refs #1533 (#1499): Personenliste (/clients/) — steht NACH dem
    // HX-Partial-Sonderfall im fetch-Handler (ein HX-Request hat dort bereits
    // mit dem Banner returnt, sonst kaperte die Voll-Shell die
    // HTMX-Livesuche-Swaps in #client-table).
    {
        matches: (url) => self.URL_PATTERNS.CLIENT_LIST.test(url),
        shell: OFFLINE_CLIENT_LIST_SHELL_URL,
    },
    // Refs #1543 (#1499): Aufgaben-Liste (/workitems/) — /workitems/new/ hat
    // im WORKITEM_NEW-Eintrag darueber bereits gewonnen.
    {
        matches: (url) => self.URL_PATTERNS.WORKITEM_LIST.test(url),
        shell: OFFLINE_WORKITEM_LIST_SHELL_URL,
    },
    // Refs #1543 (#1499): Zeitstrom-Chronik an der Wurzel-URL — isZeitstromRoot
    // prueft pathname === "/" EXAKT (nur die Startseite). Der
    // Online-Login-Redirect bleibt unberuehrt: die Kette greift ausschliesslich
    // im Netz-Fehlerfall (respondWith-catch), nie online.
    {
        matches: (url) =>
            Boolean(self.URL_PATTERNS.isZeitstromRoot && self.URL_PATTERNS.isZeitstromRoot(url)),
        shell: OFFLINE_ZEITSTROM_SHELL_URL,
    },
];

// Refs #1828: gecachten Shell servieren; ohne Cache-Treffer die /offline/-Home.
async function _serveShell(shellUrl) {
    await shellLanguageUpdate.catch(() => {});
    const preference = await caches.match(SHELL_LANGUAGE_KEY);
    const language = preference ? await preference.text() : "de";
    const selected = ["de", "en"].includes(language) ? language : "de";
    const shell = await caches.match(`${shellUrl}?shell_language=${selected}`);
    const fallback = shell || await caches.match(`${OFFLINE_FALLBACK_URL}?shell_language=${selected}`);
    if (fallback) return fallback;
    // Legacy caches are usable only before any account has selected a language.
    if (!preference) {
        return (await caches.match(shellUrl)) ||
            (await caches.match(new URL(shellUrl, self.location.origin).href)) ||
            caches.match(OFFLINE_FALLBACK_URL);
    }
    return new Response("", { status: 503, statusText: "offline" });
}

// ACK-Timeout: nach dieser Zeit gilt enqueueRequest als gescheitert
// (#662). 5s ist großzügig genug für IndexedDB + AES-GCM-Verschlüsselung
// und zugleich kurz genug, dass der User keine ewig drehende UI sieht.
const QUEUE_ACK_TIMEOUT_MS = 5000;

async function requestQueueAck(payload, clientId) {
    // Refs #1386: sucht zuerst gezielt den Client, der den Request gestellt
    // hat (fetch-Event-``clientId``) — vorher landete das QUEUE_REQUEST
    // immer beim ersten Treffer aus matchAll(), unabhaengig vom Ausloeser.
    // Nur wenn dieser Client nicht (mehr) existiert (z.B. weil clientId bei
    // Navigation-Requests leer ist), faellt die Suche auf den ersten
    // offenen Tab zurueck — nur ein Tab persistiert ohnehin die Queue.
    let client = clientId ? await self.clients.get(clientId) : null;
    if (!client) {
        const clientList = await self.clients.matchAll({ type: "window" });
        if (clientList.length === 0) {
            return { ok: false, reason: "NoClient" };
        }
        client = clientList[0];
    }
    return new Promise((resolve) => {
        const channel = new MessageChannel();
        const timer = setTimeout(() => {
            resolve({ ok: false, reason: "Timeout" });
        }, QUEUE_ACK_TIMEOUT_MS);
        channel.port1.onmessage = (event) => {
            clearTimeout(timer);
            const data = event.data || {};
            if (data.type === "QUEUE_ACK") {
                resolve({ ok: true });
            } else {
                resolve({ ok: false, reason: data.reason || "NACK" });
            }
        };
        // Der ermittelte Client bekommt den Port und meldet ACK/NACK
        // zurück. Andere Clients erhalten kein Signal, was korrekt ist (nur
        // ein Tab persistiert die Queue).
        client.postMessage(payload, [channel.port2]);
    });
}

self.addEventListener("fetch", (event) => {
    const { request } = event;

    // POST/PUT bei Netzausfall queuen (nur fuer relevante URLs)
    if (shouldQueueRequest(request)) {
        // Refs #1351 (Bug #5): A's EIGENE Replays (offline-queue._send /
        // offline-edit._postForm) tragen den Marker X-Offline-Replay. Solche
        // Requests NIE erneut abfangen/queuen — network-only durchreichen und
        // dem echten Netz/Fehler ueberlassen (der Replayer klassifiziert die
        // Antwort selbst). Ohne diesen frühen Ausstieg faenge der SW den Replay
        // bei >6s erneut ab → Doppelkanal + spurious dead-letter. Kein
        // respondWith → der Browser fuehrt den Default-Netzwerk-Fetch aus.
        if (request.headers.get("X-Offline-Replay")) {
            return;
        }

        // Multipart-Uploads NICHT queuen — die binären Daten würden im IndexedDB
        // landen und beim Replay falsch interpretiert werden.
        // Refs #1351: KEIN WRITE_FETCH_TIMEOUT_MS auf diesem Zweig — ein
        // grosser/langsamer Upload (z.B. Foto über Mobilfunk) braucht legitim
        // >6s. AbortSignal.timeout ist ein absoluter Timer ab Erstellung und
        // wuerde den Upload mitten im Stream abbrechen und faelschlich
        // "Internetverbindung erforderlich" melden — ohne Retry-/Queue-Netz
        // (Multipart wird nie gequeued). Der Lie-Fi-Timeout bleibt dem
        // queue-baren Nicht-Multipart-Schreibpfad vorbehalten (der davon
        // profitiert, weil er bei Lie-Fi schnell in die Queue faellt).
        if (isMultipart(request)) {
            event.respondWith(
                fetch(request.clone()).catch(() =>
                    _flashResponse(SW_I18N.uploadOffline, {
                        tone: "red",
                        status: 503,
                        statusText: "Offline-Upload not supported",
                    })
                )
            );
            return;
        }

        event.respondWith(
            (async () => {
                // Den vollstaendigen Body einmal aus einem Clone lesen. Er bleibt
                // unveraendert fuer Erstversuch und verschluesselte Queue erhalten.
                const body = await request.clone().text();
                const clientIdemKey = request.headers.get("X-Idempotency-Key");
                const bodyIdempotencyKey = tallyBodyIdempotencyKey(request, body);
                // Refs #1351/#1912: Prioritaet ist expliziter Header, danach
                // valider Tally-Body-Fallback, erst zuletzt ein neuer SW-Key.
                const idempotencyKey = clientIdemKey || bodyIdempotencyKey || crypto.randomUUID();
                const firstAttempt = clientIdemKey
                    ? request.clone()
                    : _withHeader(request, "X-Idempotency-Key", idempotencyKey);

                try {
                    return await fetch(firstAttempt, {
                        signal: AbortSignal.timeout(WRITE_FETCH_TIMEOUT_MS),
                    });
                } catch (_networkError) {
                    const headers = {};
                    request.headers.forEach((value, key) => {
                        if (
                            [
                                "content-type",
                                "x-csrftoken",
                                "hx-request",
                                "hx-target",
                                "hx-current-url",
                                "x-idempotency-key",
                            ].includes(key.toLowerCase())
                        ) {
                            headers[key] = value;
                        }
                    });
                    // Refs #1351 (Bug #1): denselben Key wie der Erstversuch in die
                    // Queue-Payload — hat der Client keinen gesetzt, steht der
                    // SW-generierte Key NICHT in request.headers und fehlte sonst.
                    // enqueueRequest bevorzugt diesen eingehenden Key.
                    headers["x-idempotency-key"] = idempotencyKey;

                    const ack = await requestQueueAck(
                        {
                            type: "QUEUE_REQUEST",
                            url: request.url,
                            method: request.method,
                            body: body,
                            headers: headers,
                        },
                        event.clientId
                    );

                    if (!ack.ok) {
                        // Persistieren ist gescheitert (NoSessionKey, kein
                        // offlineQueue, IndexedDB-Fehler, Timeout). Roter Banner
                        // statt stummem Datenverlust (#662).
                        return _flashResponse(
                            SW_I18N.queuePersistFailed.replace("{reason}", ack.reason),
                            {
                                tone: "red",
                                status: 503,
                                statusText: "Offline-Queue persistence failed",
                            }
                        );
                    }

                    return _flashResponse(SW_I18N.queuedOk, {
                        tone: "yellow",
                        status: 200,
                        headers: {
                            // #1926: Nur ein bestaetigtes Queue-ACK darf
                            // den DIRECT-Key fuer die naechste +1-Aktion
                            // rotieren. Der Marker ist bewusst nicht auf
                            // 503- oder echten Serverantworten vorhanden.
                            "X-Offline-Queued": "1",
                        },
                    });
                }
            })()
        );
        return;
    }

    if (request.method !== "GET") return;

    // Refs #751: Datei-/Export-Downloads laufen network-only und werden
    // weder gecacht noch durch die HTML-Offline-Fallback-Kette ersetzt.
    // Andernfalls bekäme der User für einen Download-Klick die
    // /offline/-Seite statt der Datei oder einer fachlichen
    // Fehlermeldung (404, 403, 500).
    if (
        self.URL_PATTERNS.ATTACHMENT_DOWNLOAD.test(request.url) ||
        self.URL_PATTERNS.EXPORT_DOWNLOAD.test(request.url)
    ) {
        return;
    }

    // Refs #2201: Assets werden ausschliesslich per Same-Origin +
    // statischem Pfad klassifiziert; Query-/Fragment-Anteile duerfen keinen
    // Einfluss haben. /manifest.json wird aus PWA-Scope-Gruenden wie ein
    // statisches Asset behandelt.
    const requestUrl = new URL(request.url);
    const isStaticAsset =
        requestUrl.origin === self.location.origin &&
        (requestUrl.pathname.startsWith("/static/") || requestUrl.pathname === "/manifest.json");

    if (isStaticAsset) {
        // Stale-while-revalidate: sofort aus dem Cache servieren, im
        // Hintergrund die neue Version holen und den Cache aktualisieren.
        // Offline-Fähigkeit bleibt erhalten (Cache-Fallback), aber der
        // User sieht beim nächsten Reload automatisch die aktuellen
        // Assets — kein manueller Cache-Bump pro Release mehr nötig
        // (Refs #618: alter cache-first-Ansatz hielt gefixtes JS fest).
        event.respondWith(
            caches.match(request).then((cached) => {
                const networkFetch = fetch(request, { signal: AbortSignal.timeout(READ_FETCH_TIMEOUT_MS) })
                    .then((response) => {
                        if (response && response.ok) {
                            const contentType = (response.headers.get("content-type") || "")
                                .split(";", 1)[0]
                                .trim()
                                .toLowerCase();
                            const cacheControl = (response.headers.get("cache-control") || "").toLowerCase();
                            const isManifest = requestUrl.pathname === "/manifest.json";
                            const isCacheableAsset =
                                (contentType === "text/css" ||
                                    contentType === "application/javascript" ||
                                    contentType === "text/javascript" ||
                                    contentType === "application/font-woff" ||
                                    contentType.startsWith("font/") ||
                                    contentType.startsWith("image/") ||
                                    (isManifest &&
                                        (contentType === "application/manifest+json" ||
                                            contentType === "application/json"))) &&
                                !cacheControl.includes("no-store") &&
                                !request.headers.has("HX-Request") &&
                                !contentType.startsWith("text/html");
                            if (isCacheableAsset) {
                                const clone = response.clone();
                                // Refs #1546/#2201: Laufzeit-Revalidierung landet im
                                // GETRENNTEN Runtime-Cache (nicht im Precache) und
                                // wird direkt danach auf das Eintrags-Budget
                                // gekappt. So waechst der Precache nie ueber das
                                // kuratierte APP_SHELL hinaus, und gehashte
                                // Asset-URLs akkumulieren nicht ueber
                                // Release-Generationen. Nur erlaubte Asset-Typen
                                // werden ueberhaupt gecacht; HTML/HTMX/no-store
                                // bleiben draussen.
                                caches
                                    .open(RUNTIME_CACHE_NAME)
                                    .then((cache) => cache.put(request, clone).then(() => _trimRuntimeCache()));
                            }
                        }
                        return response;
                    })
                    // Refs #1386: Cold-Start-Fix — ohne Cache-Treffer UND ohne
                    // Netz (Timeout/Fehler) lieferte respondWith(undefined)
                    // einen ungueltigen Response (Browser-Netzwerkfehler statt
                    // einer erklaerenden 503).
                    // Refs #1904: this empty 503 is built here as well and
                    // carries the server policy like every synthetic answer.
                    .catch(() => cached ?? _withServerPolicy(new Response("", { status: 503, statusText: "offline" })));
                return cached || networkFetch;
            })
        );
        return;
    }

    if (request.headers.get("Accept")?.includes("text/html") || request.headers.get("HX-Request")) {
        // Offline-Fallback-Kette (Refs #701/#1322, Refs #1828):
        // 0. HTMX-Partial-GET-Sonderfall (Refs #1416): ein Request mit
        //    HX-Request: true ist NIE eine Navigation — htmx swappt die
        //    Antwort in ein bestehendes DOM-Ziel (z.B. #client-table), statt
        //    die Seite zu ersetzen. Die Stufen darunter sind fuer Navigationen
        //    gebaut (Shells/OFFLINE_FALLBACK_URL liefern ein komplettes
        //    offline.html-Dokument); im Swap-Target eines Partials wuerde das
        //    ungestylt landen, Scripts erneut ausfuehren und per htmx'
        //    eingebauter <title>-Uebernahme (htmx.config.ignoreTitle: false)
        //    den Dokumenttitel kapern. Deshalb VOR den Shell-Stufen abzweigen.
        // 1. Versuch: Netz
        // 2. SHELL_ROUTES (Tabelle oben): der erste Treffer serviert seinen
        //    pk-losen In-Place-Shell an der kanonischen URL.
        // 3. Sonst: gecachte Version dieser URL
        // 4. Sonst: dedizierte /offline/-Home (Offline-Arbeitsplatz) statt
        //    Browser-Default „Sie sind offline / Chrome-Dino"
        event.respondWith(
            fetch(request, { signal: AbortSignal.timeout(READ_FETCH_TIMEOUT_MS) }).catch(() => {
                if (request.headers.get("HX-Request") === "true") {
                    // Kompaktes, gestyltes Banner statt der Voll-Shell — der
                    // Text kommt aus dem pro Request server-injizierten SW_I18N
                    // (Refs #1412/M17). Bewusst
                    // OHNE HX-Retarget/HX-Reswap: anders als im Write-Pfad
                    // (events.py-Konflikt-Handling, htmx_session.py) droht
                    // hier kein Datenverlust durch das Ersetzen des Targets
                    // (Such-/Filterfelder liegen ausserhalb des Swap-Ziels) —
                    // das Banner direkt an der Stelle zu zeigen, an der die
                    // aktualisierten Daten erschienen waeren, ist die
                    // naheliegendste, layoutneutrale Antwort. Bewusst KEIN
                    // _flashResponse (anderes Fragment, kein Retargeting).
                    // Refs #1904: carries the server policy of the cached
                    // offline home (see _withServerPolicy).
                    return _withServerPolicy(
                        new Response(
                            '<div class="rounded-md bg-yellow-50 p-4" role="status" data-testid="offline-partial-banner">' +
                                '<p class="text-sm text-yellow-800">' +
                                SW_I18N.partialBanner +
                                "</p>" +
                                "</div>",
                            {
                                status: 200,
                                headers: { "Content-Type": "text/html; charset=utf-8" },
                            }
                        )
                    );
                }
                for (const route of SHELL_ROUTES) {
                    if (route.matches(request.url)) {
                        return _serveShell(route.shell);
                    }
                }
                // Match public shell variants explicitly instead of bypassing
                // Cookie/Accept-Language Vary with an unrelated language.
                const pathname = new URL(request.url).pathname;
                if (SHELL_URLS.includes(pathname)) return _serveShell(pathname);
                return caches.match(request.url).then(cached => cached || _serveShell(OFFLINE_FALLBACK_URL));
            })
        );
    }
});

// Refs #1351 (M6): Kein Background-Sync-Handler mehr. Replay-Koordination
// bei Reconnect laeuft seit M6 NICHT ueber den Service Worker, sondern
// client-seitig ueber sync-orchestrator.js mit einem exklusiven Web Lock
// ("anlaufstelle-offline-mutex") + BroadcastChannel ("anlaufstelle-offline").
// Der fruehere `sync`-Event-Handler + REPLAY_QUEUE-Broadcast (notifyClients)
// sind ersatzlos entfernt: `registration.sync.register(...)` wurde nirgends
// aufgerufen — Background Sync blieb YAGNI (#1351).
