// Sign-in, logout and key revocation across pages of one browser profile.
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import { createContext, runInContext } from "node:vm";
import { IDBFactory } from "fake-indexeddb";
import { describe, expect, it, vi } from "vitest";

const cryptoSource = readFileSync("src/static/js/crypto.js", "utf8");
const bootstrapSource = readFileSync("src/static/js/auth-bootstrap.js", "utf8");
const PASSWORD = "test password";
const SALT = "c2FsdHNhbHRzYWx0";

function deferred() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
}

// The server side of one browser profile: a successful login rotates the CSRF
// secret, so a page rendered before it posts a stale token and gets a 403.
function newBrowser() {
    return { indexedDB: new IDBFactory(), secret: 1, salt: null };
}

// IndexedDB whose opens can be issued late, standing in for a busy browser:
// the read then sees whatever happened in the meantime.
function slowIndexedDB(indexedDB) {
    const control = { delayMs: 0 };
    control.factory = {
        open(...args) {
            if (!control.delayMs) return indexedDB.open(...args);
            const request = {};
            setTimeout(() => {
                const real = indexedDB.open(...args);
                const forward = (name) => (event) => {
                    request.result = real.result;
                    request.error = real.error;
                    if (request[name]) request[name](event);
                };
                real.onupgradeneeded = forward("onupgradeneeded");
                real.onsuccess = forward("onsuccess");
                real.onerror = forward("onerror");
            }, control.delayMs);
            return request;
        },
        deleteDatabase: (...args) => indexedDB.deleteDatabase(...args),
    };
    return control;
}

// One login page of the profile, rendered with the CSRF secret of its load time.
async function loginPage(browser, indexedDB = browser.indexedDB) {
    const formListeners = {};
    const documentListeners = {};
    const windowListeners = {};
    const form = {
        action: "/login/", submit: vi.fn(),
        addEventListener: (type, handler) => { (formListeners[type] ||= []).push(handler); },
    };
    const document = {
        body: { dataset: { sessionAge: "7200" } },
        querySelectorAll: () => [], readyState: "complete",
        addEventListener: (type, handler) => { (documentListeners[type] ||= []).push(handler); },
        getElementById: (id) => (id === "login-form" ? form : null),
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
        offlineStore: {
            revalidateCachedFacility: vi.fn().mockResolvedValue(),
            purgeAll: vi.fn().mockResolvedValue(),
            hasUnsyncedData: vi.fn().mockResolvedValue(false),
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
    await window.crypto_session.ready();
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
async function logOut(page, indexedDB, { clearStorage }) {
    await page.session.wipeOfflineState();
    if (clearStorage) await clearSiteData(indexedDB);
}

function clearSiteData(indexedDB) {
    return new Promise((resolve, reject) => {
        const request = indexedDB.deleteDatabase("anlaufstelle-crypto");
        request.onsuccess = resolve;
        request.onerror = () => reject(request.error);
    });
}

async function settled(page) {
    await vi.waitFor(() => expect(page.window.location.href).toBe("/"));
}

describe("second account after a logout", () => {
    it.each([false, true])("derives a key on the next login page (storage cleared: %s)", async (clearStorage) => {
        const browser = newBrowser();
        const first = await loginPage(browser);
        await first.signIn();
        expect(first.session.hasSessionKey()).toBe(true);
        await logOut(first, browser.indexedDB, { clearStorage });
        expect(await first.session.hasPersistedSessionKey()).toBe(false);

        const second = await loginPage(browser);
        await second.signIn();

        expect(second.session.hasSessionKey()).toBe(true);
        expect(await second.session.hasPersistedSessionKey()).toBe(true);
        expect(second.window.location.href).toBe("/");
        expect(second.form.submit).not.toHaveBeenCalled();
    });

    it("derives a key in a login page that was open before the other account logged out", async () => {
        const browser = newBrowser();
        const first = await loginPage(browser);
        await first.signIn();
        const waiting = await loginPage(browser);
        await logOut(first, browser.indexedDB, { clearStorage: false });
        waiting.focus();

        await waiting.signIn();

        expect(waiting.session.hasSessionKey()).toBe(true);
        expect(waiting.form.submit).not.toHaveBeenCalled();
    });

    it("derives a key in an open login page after storage was cleared", async () => {
        const browser = newBrowser();
        const first = await loginPage(browser);
        await first.signIn();
        await first.session.clearSessionKey(); // e.g. an idle wipe: marker > 0, no storage clearing
        const waiting = await loginPage(browser);
        await logOut(first, browser.indexedDB, { clearStorage: true });
        waiting.focus();

        await waiting.signIn();

        expect(waiting.session.hasSessionKey()).toBe(true);
    });

    it("derives a key when Enter follows the first key press while IndexedDB is slow", async () => {
        const browser = newBrowser();
        const slow = slowIndexedDB(browser.indexedDB);
        const first = await loginPage(browser);
        await first.signIn();
        const waiting = await loginPage(browser, slow.factory);
        await logOut(first, browser.indexedDB, { clearStorage: false });
        slow.delayMs = 30;

        waiting.keydown();
        await waiting.signIn();

        expect(waiting.session.hasSessionKey()).toBe(true);
        expect(waiting.form.submit).not.toHaveBeenCalled();
    });

    it("lands in the app without a key when the page saw no interaction, and the next login page derives", async () => {
        const browser = newBrowser();
        const first = await loginPage(browser);
        await first.signIn();
        const waiting = await loginPage(browser);
        await logOut(first, browser.indexedDB, { clearStorage: false });

        await waiting.signIn(); // the wipe after the page booted counts as a revocation
        expect(waiting.session.hasSessionKey()).toBe(false);
        expect(waiting.window.location.href).toBe("/");
        expect(waiting.loginPosts).toEqual([302]);
        expect(waiting.form.submit).not.toHaveBeenCalled();

        await logOut(waiting, browser.indexedDB, { clearStorage: true }); // logout for a new key
        const next = await loginPage(browser);
        await next.signIn();
        expect(next.session.hasSessionKey()).toBe(true);
    });
});

describe("one sign-in per login page", () => {
    it.each(["click", "Enter"])("ignores a second %s while the accepted sign-in derives its key", async (gesture) => {
        const browser = newBrowser();
        browser.salt = deferred();
        const page = await loginPage(browser);
        page.signIn();
        await vi.waitFor(() => expect(page.loginPosts).toEqual([302])); // accepted, token rotated

        if (gesture === "Enter") page.keydown();
        page.signIn();
        await new Promise((resolve) => setTimeout(resolve, 20));
        browser.salt.resolve();
        await settled(page);

        expect(page.loginPosts).toEqual([302]);
        expect(page.form.submit).not.toHaveBeenCalled();
        expect(page.session.hasSessionKey()).toBe(true);
    });
});

// A refreshed baseline must never let a derivation survive a wipe that
// happened after that derivation started.
describe("revocation stays final", () => {
    async function heldDerivation(page) {
        // Start like a login does: after a refresh that is still running.
        const refreshing = page.session.pendingRevocationRefresh && page.session.pendingRevocationRefresh();
        if (refreshing) await refreshing;
        const started = deferred();
        const resume = deferred();
        const original = page.subtle.deriveKey;
        page.subtle.deriveKey = async (...args) => {
            const key = await original(...args);
            started.resolve();
            await resume.promise;
            return key;
        };
        const attempt = page.session.beginKeyDerivation();
        const result = page.session.deriveSessionKey(PASSWORD, SALT, attempt)
            .then(() => "derived", (error) => error.name);
        await started.promise;
        return { result, resume: () => { page.subtle.deriveKey = original; resume.resolve(); } };
    }

    async function expectRejected(page, pending) {
        expect(await pending.result).toBe("NoSessionKeyError");
        expect(page.session.hasSessionKey()).toBe(false);
        expect(await page.session.hasPersistedSessionKey()).toBe(false);
    }

    it("rejects a derivation begun on a refreshed page when another tab wipes meanwhile", async () => {
        const browser = newBrowser();
        const first = await loginPage(browser);
        await first.signIn();
        const waiting = await loginPage(browser);
        await logOut(first, browser.indexedDB, { clearStorage: false });
        waiting.focus(); // baseline now follows the wipe that predates the attempt

        const pending = await heldDerivation(waiting);
        const other = await loginPage(browser);
        await other.signIn();
        await other.session.clearSessionKey(); // wipe after the attempt started
        pending.resume();

        await expectRejected(waiting, pending);
    });

    it("rejects a derivation begun before storage clearing even after the baseline refresh", async () => {
        const browser = newBrowser();
        const first = await loginPage(browser);
        await first.signIn();
        await first.session.clearSessionKey(); // marker 1
        const waiting = await loginPage(browser); // boots with marker 1

        const pending = await heldDerivation(waiting);
        await logOut(first, browser.indexedDB, { clearStorage: true }); // marker back to 0
        waiting.focus(); // baseline drops to 0, the running attempt keeps 1
        pending.resume();

        await expectRejected(waiting, pending);
    });

    it("rejects a derivation when a logout with storage clearing brings the marker back to its start value", async () => {
        const browser = newBrowser();
        const first = await loginPage(browser);
        await first.signIn();
        const waiting = await loginPage(browser); // marker 0

        const pending = await heldDerivation(waiting);
        await logOut(first, browser.indexedDB, { clearStorage: true }); // 0 -> 1 -> cleared: 0 again
        waiting.focus();
        pending.resume();

        await expectRejected(waiting, pending);
        const next = await loginPage(browser);
        await next.signIn();
        expect(next.session.hasSessionKey()).toBe(true);
    });

    it("rejects a derivation whose first marker read comes after a logout with storage clearing", async () => {
        const browser = newBrowser();
        const slow = slowIndexedDB(browser.indexedDB);
        const first = await loginPage(browser);
        await first.signIn();
        const waiting = await loginPage(browser, slow.factory); // marker 0 of the first database

        slow.delayMs = 100;
        const result = waiting.session.deriveSessionKey(PASSWORD, SALT)
            .then(() => "derived", (error) => error.name);
        await logOut(first, browser.indexedDB, { clearStorage: true }); // 0 -> 1 -> cleared
        await (await loginPage(browser)).session.ready(); // a new database with marker 0

        expect(await result).toBe("NoSessionKeyError");
        slow.delayMs = 0;
        expect(waiting.session.hasSessionKey()).toBe(false);
        expect(await waiting.session.hasPersistedSessionKey()).toBe(false);
    });

    it("rejects a derivation when a wipe after storage clearing repeats the start value", async () => {
        const browser = newBrowser();
        const first = await loginPage(browser);
        await first.signIn();
        await first.session.clearSessionKey(); // marker 1
        const waiting = await loginPage(browser); // marker 1

        const pending = await heldDerivation(waiting);
        await clearSiteData(browser.indexedDB); // marker gone
        const other = await loginPage(browser);
        await other.signIn();
        await other.session.clearSessionKey(); // legitimate wipe: marker 1 again
        pending.resume();

        await expectRejected(waiting, pending);
        const next = await loginPage(browser);
        await next.signIn();
        expect(next.session.hasSessionKey()).toBe(true);
    });
});
