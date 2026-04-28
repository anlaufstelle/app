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

const CACHE_NAME = "anlaufstelle-v5";
const APP_SHELL = [
    "/static/css/styles.css",
    "/static/icons/icon-192.png",
    "/static/icons/icon-512.png",
    "/static/icons/icon-192.svg",
    "/static/icons/icon-512.svg",
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
                                "Datei-Upload erfordert Internetverbindung. Bitte erneut versuchen, sobald Sie online sind." +
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

                await notifyClients({
                    type: "QUEUE_REQUEST",
                    url: request.url,
                    method: request.method,
                    body: body,
                    headers: headers,
                });

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
        // Offline-Fallback für Klientel-Detail: Wenn Netz weg ist, auf die
        // Offline-Viewer-Seite umleiten, die per JS aus IndexedDB rendert.
        // Der Viewer liefert "Nicht offline verfügbar", wenn kein Bundle
        // im Cache liegt — dadurch bleibt die UX konsistent (kein 502).
        event.respondWith(
            fetch(request).catch(() => {
                const clientPk =
                    self.URL_PATTERNS.extractClientPk &&
                    self.URL_PATTERNS.extractClientPk(request.url);
                if (clientPk) {
                    const offlineUrl = "/offline/clients/" + clientPk + "/";
                    return Response.redirect(offlineUrl, 302);
                }
                return caches.match(request);
            })
        );
    }
});

self.addEventListener("sync", (event) => {
    if (event.tag === "replay-offline-queue") {
        event.waitUntil(notifyClients({ type: "REPLAY_QUEUE" }));
    }
});
