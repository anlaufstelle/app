/**
 * Service Worker registration and offline queue message handler.
 */
(function () {
    'use strict';

    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/sw.js', { scope: '/' });

        // Nachrichten vom Service Worker empfangen
        navigator.serviceWorker.addEventListener('message', async function (event) {
            if (event.data.type === 'QUEUE_REQUEST') {
                // Request in IndexedDB speichern
                await window.offlineQueue.enqueueRequest(
                    event.data.url,
                    event.data.method,
                    event.data.body,
                    event.data.headers
                );
            } else if (event.data.type === 'REPLAY_QUEUE') {
                // Queue absenden (Background Sync Trigger)
                await window.offlineQueue.replayQueue();
            }
        });
    }
})();
