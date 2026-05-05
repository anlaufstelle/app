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
 * Refs #573, #576.
 */

importScripts("/static/js/url-patterns.js");

const CACHE_NAME = "anlaufstelle-v9";
// Refs #701: dediziertes Fallback-Template fuer Navigation-Requests
// ohne Cache- und Netz-Hit. Wird als App-Shell pre-cached, damit es
// auch beim ersten Offline-Aufruf garantiert verfuegbar ist.
const OFFLINE_FALLBACK_URL = "/offline/";
const APP_SHELL = [
    "/static/css/styles.css",
    "/static/icons/icon-192.png",
    "/static/icons/icon-512.png",
    "/static/icons/icon-192.svg",
    "/static/icons/icon-512.svg",
    OFFLINE_FALLBACK_URL,
];

self.addEventListener("install", (event) => {
    event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
    self.skipWaiting();
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

async function notifyClients(data) {
    const clients = await self.clients.matchAll({ type: "window" });
    clients.forEach((client) => client.postMessage(data));
}

// ACK-Timeout: nach dieser Zeit gilt enqueueRequest als gescheitert
// (#662 FND-02). 5s ist großzügig genug für IndexedDB + AES-GCM-Verschlüsselung
// und zugleich kurz genug, dass der User keine ewig drehende UI sieht.
const QUEUE_ACK_TIMEOUT_MS = 5000;

async function requestQueueAck(payload) {
    // Sucht den Client, der den Request gestellt hat (oder den ersten
    // verfügbaren), und schickt das QUEUE_REQUEST mit einem MessageChannel.
    // Auflösung: ACK -> { ok: true }, NACK/Timeout/keine Clients -> { ok: false, reason }.
    const clientList = await self.clients.matchAll({ type: "window" });
    if (clientList.length === 0) {
        return { ok: false, reason: "NoClient" };
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
        // An den ersten Client schicken — er bekommt den Port und meldet
        // ACK/NACK zurück. Andere Clients erhalten kein Signal, was korrekt
        // ist (nur ein Tab persistiert die Queue).
        clientList[0].postMessage(payload, [channel.port2]);
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
                fetch(request.clone()).catch(
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
            fetch(request.clone()).catch(async () => {
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

                const ack = await requestQueueAck({
                    type: "QUEUE_REQUEST",
                    url: request.url,
                    method: request.method,
                    body: body,
                    headers: headers,
                });

                if (!ack.ok) {
                    // Persistieren ist gescheitert (NoSessionKey, kein
                    // offlineQueue, IndexedDB-Fehler, Timeout). Roter Banner
                    // statt stummem Datenverlust (#662 FND-02).
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
                const networkFetch = fetch(request)
                    .then((response) => {
                        if (response && response.ok) {
                            const clone = response.clone();
                            caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
                        }
                        return response;
                    })
                    .catch(() => cached);
                return cached || networkFetch;
            })
        );
        return;
    }

    if (request.headers.get("Accept")?.includes("text/html") || request.headers.get("HX-Request")) {
        // Offline-Fallback-Kette (Refs #701):
        // 1. Versuch: Netz
        // 2. Klientel-Detail-Sonderfall: auf /offline/clients/<pk>/ umleiten
        //    (der Viewer rendert aus IndexedDB)
        // 3. Sonst: gecachte Version dieser URL
        // 4. Sonst: dediziertes /offline/-Fallback-Template (statt
        //    Browser-Default „Sie sind offline / Chrome-Dino")
        event.respondWith(
            fetch(request).catch(() => {
                const clientPk =
                    self.URL_PATTERNS.extractClientPk &&
                    self.URL_PATTERNS.extractClientPk(request.url);
                if (clientPk) {
                    const offlineUrl = "/offline/clients/" + clientPk + "/";
                    return Response.redirect(offlineUrl, 302);
                }
                return caches.match(request).then((cached) => cached || caches.match(OFFLINE_FALLBACK_URL));
            })
        );
    }
});

self.addEventListener("sync", (event) => {
    if (event.tag === "replay-offline-queue") {
        event.waitUntil(notifyClients({ type: "REPLAY_QUEUE" }));
    }
});
