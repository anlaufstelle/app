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
    const window = {
        crypto: { subtle }, indexedDB, addEventListener() {}, setInterval() {},
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
        window, form, session: window.crypto_session,
        signIn: () => submit({ preventDefault() {} }),
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

    // Known gap, not fixable in the login script: the revocation baseline is
    // taken from the page's boot state inside crypto.js. A login page that was
    // loaded before another tab wiped the key treats that wipe as a revocation
    // of its own, not-yet-started login.
    it.fails("derives a key in a login page that was open before the other account logged out", async () => {
        const db = new IDBFactory();
        const first = await loginPage(db);
        await first.signIn();
        const waiting = await loginPage(db);
        await logOut(first, db, { clearStorage: false });

        await waiting.signIn();

        expect(waiting.session.hasSessionKey()).toBe(true);
    });

    // Known gap, same cause: the baseline only ever grows, so storage clearing
    // (marker back to zero) leaves the open page rejecting every login.
    it.fails("keeps deriving in an open login page after storage was cleared", async () => {
        const db = new IDBFactory();
        const first = await loginPage(db);
        await first.signIn();
        await first.session.clearSessionKey(); // e.g. an idle wipe: marker > 0, no storage clearing
        const waiting = await loginPage(db);
        await logOut(first, db, { clearStorage: true });

        await waiting.signIn();
        await waiting.signIn();

        expect(waiting.session.hasSessionKey()).toBe(true);
    });
});
