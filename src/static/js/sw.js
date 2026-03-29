const CACHE_NAME = 'anlaufstelle-v3';
const APP_SHELL = [
    '/static/css/styles.css',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
    '/static/icons/icon-192.svg',
    '/static/icons/icon-512.svg',
];

// URL-Muster fuer Requests die bei Offline gequeuet werden sollen
const QUEUE_URL_PATTERNS = [
    /\/events\/new\//,
    /\/events\/\d+\/edit\//,
    /\/workitems\/[^/]+\/edit\//,
    /\/workitems\/new\//,
];

// Install: cache app shell
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
    );
    self.skipWaiting();
});

// Activate: clean old caches
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
        )
    );
    self.clients.claim();
});

/**
 * Prueft ob eine URL fuer Offline-Queuing relevant ist.
 */
function shouldQueueRequest(url) {
    return QUEUE_URL_PATTERNS.some((pattern) => pattern.test(url));
}

/**
 * Benachrichtigt den Client ueber einen gequeueten Request.
 */
async function notifyClients(data) {
    const clients = await self.clients.matchAll({ type: 'window' });
    clients.forEach((client) => {
        client.postMessage(data);
    });
}

// Fetch: network-first for HTML/HTMX, cache-first for static assets
self.addEventListener('fetch', (event) => {
    const { request } = event;

    // POST/PUT bei Netzausfall queuen (nur fuer relevante URLs)
    if ((request.method === 'POST' || request.method === 'PUT') && shouldQueueRequest(request.url)) {
        event.respondWith(
            fetch(request.clone()).catch(async () => {
                // Request-Daten fuer spaeteres Replay speichern
                const body = await request.clone().text();
                const headers = {};
                request.headers.forEach((value, key) => {
                    // Nur relevante Headers speichern
                    if (['content-type', 'x-csrftoken', 'hx-request', 'hx-target', 'hx-current-url'].includes(key.toLowerCase())) {
                        headers[key] = value;
                    }
                });

                // Client benachrichtigen damit offline-queue.js den Request speichert
                await notifyClients({
                    type: 'QUEUE_REQUEST',
                    url: request.url,
                    method: request.method,
                    body: body,
                    headers: headers,
                });

                // Erfolgs-Antwort zurueckgeben damit das UI nicht haengt
                return new Response(
                    '<div id="flash-messages">' +
                    '<div class="rounded-md bg-yellow-50 p-4 mb-4">' +
                    '<p class="text-sm text-yellow-800">' +
                    'Offline — Ihre Eingaben wurden gespeichert und werden bei Verbindung automatisch gesendet.' +
                    '</p></div></div>',
                    {
                        status: 200,
                        headers: {
                            'Content-Type': 'text/html',
                            'HX-Retarget': '#flash-messages',
                            'HX-Reswap': 'outerHTML',
                        },
                    }
                );
            })
        );
        return;
    }

    // Skip non-GET requests
    if (request.method !== 'GET') return;

    // Static assets: cache-first
    if (request.url.includes('/static/')) {
        event.respondWith(
            caches.match(request).then((cached) => cached || fetch(request).then((response) => {
                const clone = response.clone();
                caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
                return response;
            }))
        );
        return;
    }

    // HTML/HTMX: network-first
    if (request.headers.get('Accept')?.includes('text/html') || request.headers.get('HX-Request')) {
        event.respondWith(
            fetch(request).catch(() => caches.match(request))
        );
    }
});

// Background Sync: Queue absenden wenn Verbindung wieder da
self.addEventListener('sync', (event) => {
    if (event.tag === 'replay-offline-queue') {
        event.waitUntil(
            notifyClients({ type: 'REPLAY_QUEUE' })
        );
    }
});
