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
// Refs #1523 (#1499, SI-6): v19 -> v20 -- APP_SHELL um die pk-losen
// Create-Shells (OFFLINE_EVENT_SHELL_URL/OFFLINE_WORKITEM_SHELL_URL) und ihre
// Renderer (offline-create.js + der reine Feld-Helfer offline-form-fields.js)
// erweitert; der Bump erzwingt Re-Install + Re-Precache, damit der
// Kalt-Offline-Pfad (/events/new/, /workitems/new/) den neuen Erfassungs-Shell
// findet statt der /offline/-Sackgasse.
// Refs #1533 (#1499, SI-5): v20 -> v21 -- APP_SHELL um die pk-lose
// Personenlisten-Shell (OFFLINE_CLIENT_LIST_SHELL_URL = /offline/clients/) und
// ihren Renderer (offline-client-list.js) erweitert; der SW serviert die Liste
// offline IN-PLACE an der kanonischen URL /clients/ (neuer CLIENT_LIST-Zweig).
// Der Bump erzwingt Re-Install + Re-Precache, damit der Kalt-Offline-Pfad
// (/clients/) den neuen Listen-Shell findet statt der /offline/-Home.
const CACHE_NAME = "anlaufstelle-v21";
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
// Refs #1523 (#1499, SI-6): generische, pk-lose Create-Shells fuer das
// In-Place-Rendern an den kanonischen URLs /events/new/ bzw. /workitems/new/
// (offline) — statt Bounce auf /offline/. Muster wie OFFLINE_CLIENT_SHELL_URL.
const OFFLINE_EVENT_SHELL_URL = "/offline/event-shell/";
const OFFLINE_WORKITEM_SHELL_URL = "/offline/workitem-shell/";
// Refs #1533 (#1499, SI-5): generische, pk-lose Personenlisten-Shell fuer das
// In-Place-Rendern an der kanonischen URL /clients/ (offline) — statt Bounce
// auf /offline/. Muster wie OFFLINE_CLIENT_SHELL_URL; die Shell selbst ist PII-
// frei, offline-client-list.js fuellt sie aus der verschluesselten IndexedDB.
const OFFLINE_CLIENT_LIST_SHELL_URL = "/offline/clients/";
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
const APP_SHELL = [
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
    "/static/js/offline-store.js",
    "/static/js/offline-home.js",
    // Refs #1322: Der In-Place-Client-Shell rendert ueber offline-client-view.js,
    // das NUR vom Offline-Detail-Template geladen wird (sonst nirgends -> nicht
    // per stale-while-revalidate gecacht). Ohne Pre-Cache bliebe der Shell
    // offline ohne Renderer.
    "/static/js/offline-client-view.js",
    // Refs #1523 (#1499, SI-6): Die pk-losen Create-Shells (offline_create.html
    // fuer Event/WorkItem) rendern ueber offline-create.js; der reine
    // Feld-Helfer offline-form-fields.js (SI-3) speist den Event-Feld-Loop.
    // Beide werden NUR von den Create-/Detail-Templates geladen (sonst nirgends
    // -> nicht per stale-while-revalidate gecacht). Ohne Pre-Cache bliebe der
    // Kalt-Offline-Pfad (/events/new/, /workitems/new/) nach einem SW-Update
    // ohne Renderer.
    "/static/js/offline-form-fields.js",
    "/static/js/offline-create.js",
    // Refs #1533 (#1499, SI-5): Die pk-lose Personenlisten-Shell (offline_list.html)
    // rendert ueber offline-client-list.js; das Modul wird NUR vom Listen-Template
    // geladen (sonst nirgends -> nicht per stale-while-revalidate gecacht). Ohne
    // Pre-Cache bliebe der Kalt-Offline-Pfad (/clients/) nach einem SW-Update ohne
    // Renderer.
    "/static/js/offline-client-list.js",
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
    // erweitern base.html — ihr gesamtes Rendering haengt an Alpine
    // (alpine:init in offline-client-view.js/conflict-resolver.js;
    // loading/unavailable/Inhalt stehen in <template x-if>), die mobile
    // Bottom-Nav an den Layout-Komponenten (createMenu/mobileMore). Der
    // Sync-Orchestrator ist der einzige koordinierte Replay-Trigger
    // (ADR-030); ohne ihn fallen queue/edit/store auf ihre unkoordinierten
    // online-Listener zurueck (Pre-M6-Race). Nach einem CACHE_NAME-Bump ist
    // der SWR-Runtime-Cache leer — ohne Pre-Cache bleibt ein
    // Offline-Kaltstart ohne Renderer/Nav/Koordinator.
    "/static/js/alpine-csp.min.js",
    "/static/js/alpine/base-layout.js",
    "/static/js/sync-orchestrator.js",
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
    // Refs #1523 (#1499, SI-6): pk-lose Create-Shells — offline IN-PLACE an
    // /events/new/ bzw. /workitems/new/ serviert (Fallback-Zweige unten).
    OFFLINE_EVENT_SHELL_URL,
    OFFLINE_WORKITEM_SHELL_URL,
    // Refs #1533 (#1499, SI-5): pk-lose Personenlisten-Shell — offline IN-PLACE
    // an /clients/ serviert (CLIENT_LIST-Fallback-Zweig unten).
    OFFLINE_CLIENT_LIST_SHELL_URL,
    // Refs #1396: pk-lose Konflikt-Liste — public + datenlos, wird offline
    // ueber die generische caches.match(request)-Fallback-Stufe serviert.
    "/offline/conflicts/",
];

self.addEventListener("install", (event) => {
    event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
});

// Refs #1386: Update-Gate. Ein frisch installierter SW bleibt in
// "waiting", bis die Seite explizit SKIP_WAITING schickt (Klick auf
// "Neu laden" im Update-Toast, siehe sw-register.js). Ohne dieses Gate
// uebernahm der neue SW sofort (skipWaiting() ungegated im
// install-Handler) — der Toast suggerierte eine Kontrolle, die er nicht
// hatte.
self.addEventListener("message", (event) => {
    if (event.data?.type === "SKIP_WAITING") {
        self.skipWaiting();
    }
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
        )
    );
    self.clients.claim();
});

function shouldQueueRequest(url) {
    return self.URL_PATTERNS.QUEUE_PATTERNS.some((pattern) => pattern.test(url));
}

function isMultipart(request) {
    const ct = request.headers.get("content-type") || "";
    return ct.toLowerCase().startsWith("multipart/form-data");
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
    if ((request.method === "POST" || request.method === "PUT") && shouldQueueRequest(request.url)) {
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
                fetch(request.clone()).catch(
                    () =>
                        new Response(
                            '<div id="flash-messages">' +
                                '<div class="rounded-md bg-red-50 p-4 mb-4">' +
                                '<p class="text-sm text-red-800">' +
                                SW_I18N.uploadOffline +
                                "</p></div></div>",
                            {
                                status: 503,
                                statusText: "Offline-Upload not supported",
                                headers: {
                                    "Content-Type": "text/html; charset=utf-8",
                                    "HX-Retarget": "#flash-messages",
                                    "HX-Reswap": "outerHTML",
                                },
                            }
                        )
                )
            );
            return;
        }

        // Refs #1351 (Bug #1): EINEN Idempotency-Key fuer BEIDE Kanaele minten,
        // BEVOR der Erstversuch rausgeht. Denselben Key traegt (a) der
        // Erstversuch-fetch (X-Idempotency-Key-Header) und (b) — falls der
        // Erstversuch nach >6s abbricht, gunicorn aber weiter committet — der
        // in die Queue persistierte Replay. So dedupliziert der Server den
        // Slow-Commit gegen den Replay statt zwei Objekte anzulegen. Einen vom
        // Client bereits gesetzten Key NICHT ueberschreiben (offline-edit-/
        // Queue-Replays bringen ihren eigenen mit).
        const clientIdemKey = request.headers.get("X-Idempotency-Key");
        const idempotencyKey = clientIdemKey || crypto.randomUUID();
        const firstAttempt = clientIdemKey
            ? request.clone()
            : _withHeader(request, "X-Idempotency-Key", idempotencyKey);

        event.respondWith(
            fetch(firstAttempt, { signal: AbortSignal.timeout(WRITE_FETCH_TIMEOUT_MS) }).catch(async () => {
                const body = await request.clone().text();
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
                    return new Response(
                        '<div id="flash-messages">' +
                            '<div class="rounded-md bg-red-50 p-4 mb-4">' +
                            '<p class="text-sm text-red-800">' +
                            SW_I18N.queuePersistFailed.replace("{reason}", ack.reason) +
                            "</p></div></div>",
                        {
                            status: 503,
                            statusText: "Offline-Queue persistence failed",
                            headers: {
                                "Content-Type": "text/html; charset=utf-8",
                                "HX-Retarget": "#flash-messages",
                                "HX-Reswap": "outerHTML",
                            },
                        }
                    );
                }

                return new Response(
                    '<div id="flash-messages">' +
                        '<div class="rounded-md bg-yellow-50 p-4 mb-4">' +
                        '<p class="text-sm text-yellow-800">' +
                        SW_I18N.queuedOk +
                        "</p></div></div>",
                    {
                        status: 200,
                        headers: {
                            "Content-Type": "text/html; charset=utf-8",
                            "HX-Retarget": "#flash-messages",
                            "HX-Reswap": "outerHTML",
                        },
                    }
                );
            })
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

    // Refs #1334: /manifest.json liegt aus PWA-Scope-Gruenden nicht unter
    // /static/, soll aber wie ein statisches Asset offline aus dem Cache
    // kommen (gleiche stale-while-revalidate-Behandlung).
    if (request.url.includes("/static/") || new URL(request.url).pathname === "/manifest.json") {
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
                            const clone = response.clone();
                            caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
                        }
                        return response;
                    })
                    // Refs #1386: Cold-Start-Fix — ohne Cache-Treffer UND ohne
                    // Netz (Timeout/Fehler) lieferte respondWith(undefined)
                    // einen ungueltigen Response (Browser-Netzwerkfehler statt
                    // einer erklaerenden 503).
                    .catch(() => cached ?? new Response("", { status: 503, statusText: "offline" }));
                return cached || networkFetch;
            })
        );
        return;
    }

    if (request.headers.get("Accept")?.includes("text/html") || request.headers.get("HX-Request")) {
        // Offline-Fallback-Kette (Refs #701/#1322):
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
        // 2. Klientel-Detail-Sonderfall: den gecachten, pk-losen Client-Shell
        //    IN-PLACE servieren (200, KEIN Redirect) — die URL bleibt
        //    kanonisch (/clients/<pk>/), offline-client-view.js liest die pk
        //    aus location.pathname und rendert aus IndexedDB.
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
                    // naheliegendste, layoutneutrale Antwort.
                    return new Response(
                        '<div class="rounded-md bg-yellow-50 p-4" role="status" data-testid="offline-partial-banner">' +
                            '<p class="text-sm text-yellow-800">' +
                            SW_I18N.partialBanner +
                            "</p>" +
                            "</div>",
                        {
                            status: 200,
                            headers: { "Content-Type": "text/html; charset=utf-8" },
                        }
                    );
                }
                const clientPk =
                    self.URL_PATTERNS.extractClientPk &&
                    self.URL_PATTERNS.extractClientPk(request.url);
                if (clientPk) {
                    return caches
                        .match(OFFLINE_CLIENT_SHELL_URL)
                        .then((shell) => shell || caches.match(OFFLINE_FALLBACK_URL));
                }
                // Refs #1396: Konflikt-Review-Sonderfall — den gecachten,
                // pk-losen Konflikt-Shell IN-PLACE servieren (200, KEIN
                // Redirect); conflict-resolver.js liest die event-pk aus
                // location.pathname. Die pk-lose Liste (/offline/conflicts/)
                // matcht hier bewusst NICHT und faellt in die generische
                // caches.match(request)-Stufe (sie ist selbst pre-cached).
                const conflictPk =
                    self.URL_PATTERNS.extractConflictPk &&
                    self.URL_PATTERNS.extractConflictPk(request.url);
                if (conflictPk) {
                    return caches
                        .match(OFFLINE_CONFLICT_SHELL_URL)
                        .then((shell) => shell || caches.match(OFFLINE_FALLBACK_URL));
                }
                // Refs #1523 (#1499, SI-6): Create-Formular-Sonderfall — den
                // gecachten, pk-losen Event-/WorkItem-Create-Shell IN-PLACE an
                // der kanonischen URL (/events/new/ bzw. /workitems/new/)
                // servieren (200, KEIN Redirect); offline-create.js liest den
                // Katalog + die mitgenommenen Personen aus der verschluesselten
                // IndexedDB. Diese Zweige stehen VOR der generischen
                // caches.match(request.url)-Stufe, die sonst die /offline/-Home
                // statt der echten Erfassungs-Shell liefern wuerde. Muster wie
                // die OFFLINE_*_SHELL_URL-Zweige darueber.
                if (self.URL_PATTERNS.EVENT_NEW.test(request.url)) {
                    return caches
                        .match(OFFLINE_EVENT_SHELL_URL)
                        .then((shell) => shell || caches.match(OFFLINE_FALLBACK_URL));
                }
                if (self.URL_PATTERNS.WORKITEM_NEW.test(request.url)) {
                    return caches
                        .match(OFFLINE_WORKITEM_SHELL_URL)
                        .then((shell) => shell || caches.match(OFFLINE_FALLBACK_URL));
                }
                // Refs #1533 (#1499, SI-5): Personenlisten-Sonderfall — den
                // gecachten, pk-losen Listen-Shell IN-PLACE an der kanonischen
                // URL /clients/ servieren (200, KEIN Redirect); offline-client-
                // list.js liest die mitgenommenen Personen aus der
                // verschluesselten IndexedDB. Dieser Zweig steht bewusst NACH dem
                // HTMX-Such-Swap-Sonderfall oben (ein HX-Request hat da bereits
                // mit dem Partial-Banner returnt — sonst kaperte die Voll-Shell
                // die HTMX-Livesuche-Swaps in #client-table) und VOR der
                // generischen caches.match(request.url)-Stufe, die sonst die
                // /offline/-Home statt der echten Liste liefern wuerde. Muster
                // wie die OFFLINE_*_SHELL_URL-Zweige darueber.
                if (self.URL_PATTERNS.CLIENT_LIST.test(request.url)) {
                    return caches
                        .match(OFFLINE_CLIENT_LIST_SHELL_URL)
                        .then((shell) => shell || caches.match(OFFLINE_FALLBACK_URL));
                }
                // Refs #1396: nach der URL (nicht dem Live-Request) matchen. Die
                // pre-cachten HTML-Shells (z.B. /offline/conflicts/) tragen ein
                // Django-``Vary: Cookie, Accept-Language``; der Install-Fetch
                // (cache.addAll) speichert sie mit LEEREN Vary-Headern, waehrend
                // die Offline-Navigation Accept-Language/Cookie mitschickt —
                // caches.match(request) verfehlt sie dadurch (Vary-Mismatch) und
                // fiele faelschlich auf /offline/ zurueck. Der URL-String-Match
                // ignoriert die Live-Header und trifft den Precache zuverlaessig
                // (Muster wie die OFFLINE_*_SHELL_URL-Zweige darueber).
                return caches
                    .match(request.url)
                    .then((cached) => cached || caches.match(OFFLINE_FALLBACK_URL));
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
