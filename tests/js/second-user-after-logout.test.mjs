// Sign-in, logout and key revocation across pages of one browser profile.
import { describe, expect, it, vi } from "vitest";
import {
    PASSWORD, SALT, clearSiteData, deferred, logOut, loginPage, newBrowser, settled, slowIndexedDB,
} from "./_login_page_harness.mjs";

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
        const waiting = await loginPage(browser, { indexedDB: slow.factory });
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
        const refreshing = page.session.prepareKeyDerivation && page.session.prepareKeyDerivation();
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
        const waiting = await loginPage(browser, { indexedDB: slow.factory }); // marker 0 of the first database

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
