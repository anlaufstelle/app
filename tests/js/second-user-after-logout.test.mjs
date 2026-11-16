// A second account signing in after another account's logout in the same browser.
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

// One login page of the shared browser profile, backed by the shared IndexedDB.
async function loginPage(indexedDB) {
    let submit;
    const form = {
        action: "/login/", submit: vi.fn(),
        addEventListener: (_type, handler) => { submit = handler; },
    };
    const document = {
        body: { dataset: { sessionAge: "7200" } },
        querySelectorAll: () => [], addEventListener() {}, readyState: "complete",
        getElementById: (id) => (id === "login-form" ? form : null),
        querySelector: () => null,
    };
    const subtle = {
        importKey: webcrypto.subtle.importKey.bind(webcrypto.subtle),
        deriveKey: webcrypto.subtle.deriveKey.bind(webcrypto.subtle),
    };
    const listeners = {};
    const window = {
        crypto: { subtle }, indexedDB, setInterval() {},
        addEventListener: (type, handler) => { (listeners[type] ||= []).push(handler); },
        getCsrfToken: () => "csrf", location: { pathname: "/login/" },
        offlineStore: {
            revalidateCachedFacility: vi.fn().mockResolvedValue(),
            purgeAll: vi.fn().mockResolvedValue(),
            hasUnsyncedData: vi.fn().mockResolvedValue(false),
        },
    };
    const fetch = vi.fn(async (url, options = {}) => {
        if (options.method === "POST" && String(url).includes("/auth/offline-key-salt/")) {
            return { ok: true, json: async () => ({ salt: SALT, home_url: "/", offline_key_max_age: 3600 }) };
        }
        if (options.method === "POST") return { status: 302, type: "opaqueredirect", ok: false };
        return { ok: true, text: async () => '<meta name="csrf-token" content="fresh">' };
    });
    const context = createContext({
        window, document, indexedDB, TextEncoder, TextDecoder, atob, btoa, console, fetch,
        FormData: class { get() { return PASSWORD; } },
    });
    runInContext(cryptoSource, context);
    await window.crypto_session.ready();
    runInContext(bootstrapSource, context);
    return {
        window, form, subtle, session: window.crypto_session,
        signIn: () => submit({ preventDefault() {} }),
        // The user comes back to this page (tab switch, window focus).
        async focus() {
            for (const handler of listeners.focus || []) handler();
            await new Promise((resolve) => setTimeout(resolve, 20));
        },
    };
}

// Logout as the page does it: wipe the offline state, then the response's
// storage clearing removes the key database.
async function logOut(page, indexedDB, { clearStorage }) {
    await page.session.wipeOfflineState();
    if (clearStorage) {
        await new Promise((resolve, reject) => {
            const request = indexedDB.deleteDatabase("anlaufstelle-crypto");
            request.onsuccess = resolve;
            request.onerror = () => reject(request.error);
        });
    }
}

describe("second account after a logout", () => {
    it.each([false, true])("derives a key on the next login page (storage cleared: %s)", async (clearStorage) => {
        const db = new IDBFactory();
        const first = await loginPage(db);
        await first.signIn();
        expect(first.session.hasSessionKey()).toBe(true);
        await logOut(first, db, { clearStorage });
        expect(await first.session.hasPersistedSessionKey()).toBe(false);

        const second = await loginPage(db);
        await second.signIn();

        expect(second.session.hasSessionKey()).toBe(true);
        expect(await second.session.hasPersistedSessionKey()).toBe(true);
        expect(second.window.location.href).toBe("/");
        expect(second.form.submit).not.toHaveBeenCalled();
    });

    it("derives a key in a login page that was open before the other account logged out", async () => {
        const db = new IDBFactory();
        const first = await loginPage(db);
        await first.signIn();
        const waiting = await loginPage(db);
        await logOut(first, db, { clearStorage: false });
        await waiting.focus();

        await waiting.signIn();

        expect(waiting.session.hasSessionKey()).toBe(true);
        expect(waiting.form.submit).not.toHaveBeenCalled();
    });

    it("derives a key in an open login page after storage was cleared", async () => {
        const db = new IDBFactory();
        const first = await loginPage(db);
        await first.signIn();
        await first.session.clearSessionKey(); // e.g. an idle wipe: marker > 0, no storage clearing
        const waiting = await loginPage(db);
        await logOut(first, db, { clearStorage: true });
        await waiting.focus();

        await waiting.signIn();

        expect(waiting.session.hasSessionKey()).toBe(true);
    });

    it("recovers on the next attempt even when the page never saw the clearing", async () => {
        const db = new IDBFactory();
        const first = await loginPage(db);
        await first.signIn();
        await first.session.clearSessionKey();
        const waiting = await loginPage(db);
        await logOut(first, db, { clearStorage: true });

        await waiting.signIn(); // rejected: the clearing happened after the page booted
        expect(waiting.session.hasSessionKey()).toBe(false);
        await waiting.signIn();

        expect(waiting.session.hasSessionKey()).toBe(true);
    });
});

// The refreshed baseline must never let a derivation survive a wipe that
// happened after that derivation started.
describe("revocation stays final", () => {
    async function heldDerivation(page) {
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

    it("rejects a derivation begun on a refreshed page when another tab wipes meanwhile", async () => {
        const db = new IDBFactory();
        const first = await loginPage(db);
        await first.signIn();
        const waiting = await loginPage(db);
        await logOut(first, db, { clearStorage: false });
        await waiting.focus(); // baseline now follows the wipe that predates the attempt

        const pending = await heldDerivation(waiting);
        const other = await loginPage(db);
        await other.signIn();
        await other.session.clearSessionKey(); // wipe after the attempt started
        pending.resume();

        expect(await pending.result).toBe("NoSessionKeyError");
        expect(waiting.session.hasSessionKey()).toBe(false);
        expect(await waiting.session.hasPersistedSessionKey()).toBe(false);
    });

    it("rejects a derivation begun before storage clearing even after the baseline refresh", async () => {
        const db = new IDBFactory();
        const first = await loginPage(db);
        await first.signIn();
        await first.session.clearSessionKey(); // marker 1
        const waiting = await loginPage(db); // boots with marker 1

        const pending = await heldDerivation(waiting);
        await logOut(first, db, { clearStorage: true }); // marker back to 0
        await waiting.focus(); // baseline drops to 0, the running attempt keeps 1
        pending.resume();

        expect(await pending.result).toBe("NoSessionKeyError");
        expect(waiting.session.hasSessionKey()).toBe(false);
        expect(await waiting.session.hasPersistedSessionKey()).toBe(false);
    });
});
