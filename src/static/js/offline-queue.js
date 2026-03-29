/**
 * Offline-Queue: Speichert POST/PUT-Requests bei Netzausfall in IndexedDB
 * und sendet sie bei Wiederverbindung erneut.
 */
(function () {
    'use strict';

    const DB_NAME = 'anlaufstelle-offline';
    const DB_VERSION = 1;
    const STORE_NAME = 'requests';

    /** IndexedDB oeffnen/erstellen. */
    function openDB() {
        return new Promise((resolve, reject) => {
            const req = indexedDB.open(DB_NAME, DB_VERSION);
            req.onupgradeneeded = () => {
                const db = req.result;
                if (!db.objectStoreNames.contains(STORE_NAME)) {
                    db.createObjectStore(STORE_NAME, { keyPath: 'id', autoIncrement: true });
                }
            };
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
        });
    }

    /** Request in IndexedDB speichern. */
    async function enqueueRequest(url, method, body, headers) {
        const db = await openDB();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(STORE_NAME, 'readwrite');
            const store = tx.objectStore(STORE_NAME);
            store.add({
                url: url,
                method: method,
                body: body,
                headers: headers,
                timestamp: Date.now(),
            });
            tx.oncomplete = () => {
                db.close();
                _updateQueueCount();
                resolve();
            };
            tx.onerror = () => {
                db.close();
                reject(tx.error);
            };
        });
    }

    /** Anzahl der gespeicherten Requests zurueckgeben. */
    async function getQueueCount() {
        try {
            const db = await openDB();
            return new Promise((resolve) => {
                const tx = db.transaction(STORE_NAME, 'readonly');
                const store = tx.objectStore(STORE_NAME);
                const countReq = store.count();
                countReq.onsuccess = () => {
                    db.close();
                    resolve(countReq.result);
                };
                countReq.onerror = () => {
                    db.close();
                    resolve(0);
                };
            });
        } catch {
            return 0;
        }
    }

    /** Alle gespeicherten Requests abrufen. */
    async function getAllQueued() {
        const db = await openDB();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(STORE_NAME, 'readonly');
            const store = tx.objectStore(STORE_NAME);
            const req = store.getAll();
            req.onsuccess = () => {
                db.close();
                resolve(req.result);
            };
            req.onerror = () => {
                db.close();
                reject(req.error);
            };
        });
    }

    /** Einen einzelnen Eintrag nach ID loeschen. */
    async function deleteEntry(id) {
        const db = await openDB();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(STORE_NAME, 'readwrite');
            const store = tx.objectStore(STORE_NAME);
            store.delete(id);
            tx.oncomplete = () => {
                db.close();
                resolve();
            };
            tx.onerror = () => {
                db.close();
                reject(tx.error);
            };
        });
    }

    /** Aktuellen CSRF-Token aus dem Cookie lesen. */
    function _getCSRFToken() {
        const match = document.cookie.match(/csrftoken=([^;]+)/);
        return match ? match[1] : null;
    }

    /** Aktuellen CSRF-Token vom Server holen (Fallback). */
    async function _fetchCSRFToken() {
        try {
            const resp = await fetch('/login/', { method: 'GET', credentials: 'same-origin' });
            if (resp.ok) {
                const match = document.cookie.match(/csrftoken=([^;]+)/);
                return match ? match[1] : null;
            }
        } catch {
            // Netzwerk noch nicht verfuegbar
        }
        return null;
    }

    /** Queue-Zaehler im Alpine-Store aktualisieren. */
    async function _updateQueueCount() {
        const count = await getQueueCount();
        window.dispatchEvent(new CustomEvent('offline-queue-count', { detail: { count: count } }));
    }

    /**
     * Gespeicherte Requests absenden wenn online.
     * CSRF-Token wird vor dem Replay aktualisiert.
     */
    async function replayQueue() {
        if (!navigator.onLine) return;

        const items = await getAllQueued();
        if (items.length === 0) return;

        // Aktuellen CSRF-Token holen
        let csrfToken = _getCSRFToken();
        if (!csrfToken) {
            csrfToken = await _fetchCSRFToken();
        }

        for (const item of items) {
            try {
                // CSRF-Token im Header aktualisieren
                const headers = Object.assign({}, item.headers);
                if (csrfToken) {
                    headers['X-CSRFToken'] = csrfToken;
                }

                await fetch(item.url, {
                    method: item.method,
                    body: item.body,
                    headers: headers,
                    credentials: 'same-origin',
                });
                await deleteEntry(item.id);
            } catch {
                // Netzwerk wieder weg — abbrechen, Rest bleibt in der Queue
                break;
            }
        }

        _updateQueueCount();
    }

    // Event-Listener: Beim Online-Event Queue absenden
    window.addEventListener('online', () => {
        replayQueue();
    });

    // Queue-Count beim Laden pruefen
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _updateQueueCount);
    } else {
        _updateQueueCount();
    }

    // API exportieren
    window.offlineQueue = {
        enqueueRequest: enqueueRequest,
        replayQueue: replayQueue,
        getQueueCount: getQueueCount,
    };
})();
