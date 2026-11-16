// Login pages of one browser profile: real crypto.js and auth-bootstrap.js in
// separate VM contexts sharing one fake IndexedDB and one modelled server.
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import { createContext, runInContext } from "node:vm";
import { IDBFactory } from "fake-indexeddb";
import { expect, vi } from "vitest";

const cryptoSource = readFileSync("src/static/js/crypto.js", "utf8");
const bootstrapSource = readFileSync("src/static/js/auth-bootstrap.js", "utf8");
export const PASSWORD = "test password";
export const SALT = "c2FsdHNhbHRzYWx0";
const DB_NAME = "anlaufstelle-crypto";

export function deferred() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
}

export const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// The server side of one browser profile: a successful login rotates the CSRF
// secret, so a page rendered before it posts a stale token and gets a 403.
export function newBrowser() {
    return { indexedDB: new IDBFactory(), secret: 1, salt: null };
}

// IndexedDB whose opens can be issued late, standing in for a busy browser:
// the read then sees whatever happened in the meantime. ``delayMs`` issues
// each open later; ``hold()`` queues them until ``release()``.
export function slowIndexedDB(indexedDB) {
    const queue = [];
    const control = { delayMs: 0, holding: false };
    control.hold = () => { control.holding = true; };
    control.release = () => {
        control.holding = false;
        for (const start of queue.splice(0)) start();
    };
    control.factory = {
        open(...args) {
            if (!control.delayMs && !control.holding) return indexedDB.open(...args);
            const request = {};
            const start = () => {
                const real = indexedDB.open(...args);
                const forward = (name) => (event) => {
                    request.result = real.result;
                    request.error = real.error;
                    if (request[name]) request[name](event);
                };
                real.onupgradeneeded = forward("onupgradeneeded");
                real.onsuccess = forward("onsuccess");
                real.onerror = forward("onerror");
            };
            if (control.holding) queue.push(start);
            else setTimeout(start, control.delayMs);
            return request;
        },
        deleteDatabase: (...args) => indexedDB.deleteDatabase(...args),
    };
    return control;
}

// One page of the profile, rendered with the CSRF secret of its load time.
// ``cold`` skips waiting for the boot hydration; ``formId`` picks the form.
export async function loginPage(browser, { indexedDB = browser.indexedDB, cold = false, formId = "login-form" } = {}) {
    const formListeners = {};
    const documentListeners = {};
    const windowListeners = {};
    const form = {
        action: formId === "login-form" ? "/login/" : "/password-change/", submit: vi.fn(),
        addEventListener: (type, handler) => { (formListeners[type] ||= []).push(handler); },
        getAttribute: () => null,
        // Programmatic submit as the browser runs it: every listener until one stops it.
        requestSubmit() {
            let stopped = false;
            const event = { preventDefault() {}, stopImmediatePropagation() { stopped = true; } };
            for (const handler of formListeners.submit || []) {
                handler(event);
                if (stopped) break;
            }
        },
    };
    const document = {
        body: { dataset: { sessionAge: "7200" } },
        querySelectorAll: () => [], readyState: "complete",
        addEventListener: (type, handler) => { (documentListeners[type] ||= []).push(handler); },
        getElementById: (id) => (id === formId ? form : null),
        querySelector: () => null,
    };
    const subtle = {
        importKey: webcrypto.subtle.importKey.bind(webcrypto.subtle),
        deriveKey: webcrypto.subtle.deriveKey.bind(webcrypto.subtle),
    };
    const window = {
        crypto: { subtle }, indexedDB, setInterval() {},
        addEventListener: (type, handler) => { (windowListeners[type] ||= []).push(handler); },
        getCsrfToken: () => "csrf", location: { pathname: "/login/" },
        confirm: () => true,
        offlineStore: {
            revalidateCachedFacility: vi.fn().mockResolvedValue(),
            purgeAll: vi.fn().mockResolvedValue(),
            hasUnsyncedData: vi.fn().mockResolvedValue(false),
            countUnsyncedEvents: vi.fn().mockResolvedValue(0),
            countQueueByStatus: vi.fn().mockResolvedValue({ total: 0 }),
        },
    };
    const pageToken = browser.secret;
    const loginPosts = [];
    const fetch = vi.fn(async (url, options = {}) => {
        if (options.method === "POST" && String(url).includes("/auth/offline-key-salt/")) {
            if (browser.salt) await browser.salt.promise;
            return { ok: true, json: async () => ({ salt: SALT, home_url: "/", offline_key_max_age: 3600 }) };
        }
        if (options.method === "POST") {
            const status = pageToken === browser.secret ? 302 : 403;
            loginPosts.push(status);
            if (status === 403) return { status, type: "basic", ok: false };
            browser.secret += 1;
            return { status, type: "opaqueredirect", ok: false };
        }
        return { ok: true, text: async () => `<meta name="csrf-token" content="${browser.secret}">` };
    });
    const context = createContext({
        window, document, indexedDB, TextEncoder, TextDecoder, atob, btoa, console, fetch, setTimeout,
        FormData: class { get() { return PASSWORD; } },
    });
    runInContext(cryptoSource, context);
    if (!cold) await window.crypto_session.ready();
    runInContext(bootstrapSource, context);
    const fire = (listeners, type) => (listeners[type] || []).forEach((handler) => handler({ type }));
    return {
        window, form, subtle, loginPosts, session: window.crypto_session,
        // One submit event (click on "Anmelden" or Enter); returns the handler result.
        signIn: () => (formListeners.submit || []).map((handler) => handler({ preventDefault() {} })).at(-1),
        // The user comes back to this page (tab switch, window focus).
        focus: () => fire(windowListeners, "focus"),
        // A key press inside the form, e.g. Enter right before the submit.
        keydown() {
            fire(formListeners, "keydown");
            fire(documentListeners, "keydown");
        },
    };
}

// Logout as the page does it: wipe the offline state, then the response's
// storage clearing removes the key database.
export async function logOut(page, indexedDB, { clearStorage }) {
    await page.session.wipeOfflineState();
    if (clearStorage) await clearSiteData(indexedDB);
}

export function clearSiteData(indexedDB) {
    return new Promise((resolve, reject) => {
        const request = indexedDB.deleteDatabase(DB_NAME);
        request.onsuccess = resolve;
        request.onerror = () => reject(request.error);
    });
}

export async function settled(page) {
    await vi.waitFor(() => expect(page.window.location.href).toBe("/"));
}

export async function keyState(page) {
    return { memory: page.session.hasSessionKey(), persisted: await page.session.hasPersistedSessionKey() };
}

// The key module of earlier releases: its database has no marker row, and a
// wipe writes the marker without an epoch.
function rawTransaction(indexedDB, work) {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, 1);
        request.onupgradeneeded = () => request.result.createObjectStore("meta", { keyPath: "key" });
        request.onerror = () => reject(request.error);
        request.onsuccess = () => {
            const db = request.result;
            const tx = db.transaction("meta", "readwrite");
            work(tx.objectStore("meta"));
            tx.oncomplete = () => { db.close(); resolve(); };
            tx.onerror = tx.onabort = () => { db.close(); reject(tx.error); };
        };
    });
}

export const legacyDatabase = (indexedDB) => rawTransaction(indexedDB, () => {});

export const legacyWipe = (indexedDB) => rawTransaction(indexedDB, (store) => {
    store.delete("sessionKey");
    const read = store.get("keyRevocation");
    read.onsuccess = () => store.put({ key: "keyRevocation", generation: (read.result ? read.result.generation : 0) + 1 });
});
