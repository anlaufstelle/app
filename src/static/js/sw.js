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

const CACHE_NAME = "anlaufstelle-v12";
// Refs #701: dediziertes Fallback-Template fuer Navigation-Requests
// ohne Cache- und Netz-Hit. Wird als App-Shell pre-cached, damit es
// auch beim ersten Offline-Aufruf garantiert verfuegbar ist.
const OFFLINE_FALLBACK_URL = "/offline/";
// Refs #1322: generischer, pk-loser Shell fuer In-Place-Rendern an der
// kanonischen URL /clients/<pk>/ (offline) — statt Redirect auf /offline/...
const OFFLINE_CLIENT_SHELL_URL = "/offline/client-shell/";
// Refs #1386: Timeouts gegen Lie-Fi (Verbindung meldet sich als "online",
// haengt aber ohne Antwort/Fehler). Ohne Timeout haengt respondWith()
// endlos, statt in die vorhandenen Queue-/Offline-Fallback-Ketten zu laufen.
const WRITE_FETCH_TIMEOUT_MS = 6000;
const READ_FETCH_TIMEOUT_MS = 8000;
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
    // Refs #1386: Diese Module treiben den Offline-Sync-Kern (CSRF-Refresh,
    // URL-Whitelist, Queue, "Offline mitnehmen"-Client-Cache, Event-Edit-
    // Replay) und werden auch von Seiten geladen, die selbst NICHT im
    // APP_SHELL stehen (z.B. Client-Liste/-Detail) — ohne Pre-Cache waeren
    // sie beim ersten Offline-Aufruf einer noch nicht besuchten Seite nicht
    // ladbar.
    "/static/js/csrf.js",
    "/static/js/url-patterns.js",
    "/static/js/offline-queue.js",
    "/static/js/offline-client.js",
    "/static/js/offline-edit.js",
    OFFLINE_FALLBACK_URL,
    OFFLINE_CLIENT_SHELL_URL,
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
        // Multipart-Uploads NICHT queuen — die binären Daten würden im IndexedDB
        // landen und beim Replay falsch interpretiert werden.
        if (isMultipart(request)) {
            event.respondWith(
                fetch(request.clone(), { signal: AbortSignal.timeout(WRITE_FETCH_TIMEOUT_MS) }).catch(
                    () =>
                        new Response(
                            '<div id="flash-messages">' +
                                '<div class="rounded-md bg-red-50 p-4 mb-4">' +
                                '<p class="text-sm text-red-800">' +
                                "Offline — Datei-Uploads erfordern eine Internetverbindung. " +
                                "Bitte erneut versuchen, sobald Sie online sind." +
                                "</p></div></div>",
                            {
                                status: 503,
                                statusText: "Offline-Upload not supported",
                                headers: {
                                    "Content-Type": "text/html",
                                    "HX-Retarget": "#flash-messages",
                                    "HX-Reswap": "outerHTML",
                                },
                            }
                        )
                )
            );
            return;
        }

        event.respondWith(
            fetch(request.clone(), { signal: AbortSignal.timeout(WRITE_FETCH_TIMEOUT_MS) }).catch(async () => {
                const body = await request.clone().text();
                const headers = {};
                request.headers.forEach((value, key) => {
                    if (
                        ["content-type", "x-csrftoken", "hx-request", "hx-target", "hx-current-url"].includes(
                            key.toLowerCase()
                        )
                    ) {
                        headers[key] = value;
                    }
                });

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
                            "Offline — Ihre Eingaben konnten nicht lokal gespeichert werden (" +
                            ack.reason +
                            "). Bitte erneut versuchen, sobald Sie online sind." +
                            "</p></div></div>",
                        {
                            status: 503,
                            statusText: "Offline-Queue persistence failed",
                            headers: {
                                "Content-Type": "text/html",
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
                        "Offline — Ihre Eingaben wurden lokal verschlüsselt und werden bei Verbindung automatisch gesendet." +
                        "</p></div></div>",
                    {
                        status: 200,
                        headers: {
                            "Content-Type": "text/html",
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

    if (request.url.includes("/static/")) {
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
                const clientPk =
                    self.URL_PATTERNS.extractClientPk &&
                    self.URL_PATTERNS.extractClientPk(request.url);
                if (clientPk) {
                    return caches
                        .match(OFFLINE_CLIENT_SHELL_URL)
                        .then((shell) => shell || caches.match(OFFLINE_FALLBACK_URL));
                }
                return caches.match(request).then((cached) => cached || caches.match(OFFLINE_FALLBACK_URL));
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
