// Key revocation at its boundaries: a page that has not read the marker yet,
// databases of earlier releases and slow storage.
import { describe, expect, it, vi } from "vitest";
import {
    PASSWORD, SALT, clearSiteData, deferred, keyState, legacyDatabase, legacyWipe, logOut, loginPage,
    newBrowser, slowIndexedDB, wait,
} from "./_login_page_harness.mjs";

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
    const result = page.session.deriveSessionKey(PASSWORD, SALT).then(() => "derived", (error) => error.name);
    await started.promise;
    return { result, resume: () => { page.subtle.deriveKey = original; resume.resolve(); } };
}

describe("a page that has not read the revocation marker yet", () => {
    it("rejects a derivation begun before the first marker read when a logout with storage clearing follows", async () => {
        const browser = newBrowser();
        const owner = await loginPage(browser);
        await owner.signIn();
        const slow = slowIndexedDB(browser.indexedDB);
        slow.hold();
        const cold = await loginPage(browser, { indexedDB: slow.factory, cold: true });

        const attempt = cold.session.beginKeyDerivation(); // as login and password change begin
        const result = cold.session.deriveSessionKey(PASSWORD, SALT, attempt)
            .then(() => "derived", (error) => error.name);
        await logOut(owner, browser.indexedDB, { clearStorage: true });
        slow.release();

        expect(await result).toBe("NoSessionKeyError");
        expect(await keyState(cold)).toEqual({ memory: false, persisted: false });
    });

    it("signs in without a key, and never twice, when storage stays busy past the preparation", async () => {
        const browser = newBrowser();
        const owner = await loginPage(browser);
        await owner.signIn();
        const slow = slowIndexedDB(browser.indexedDB);
        slow.hold();
        const cold = await loginPage(browser, { indexedDB: slow.factory, cold: true });

        cold.keydown();
        const submit = cold.signIn();
        await wait(3500); // past the bounded wait: login and salt request go out
        expect(cold.loginPosts).toEqual([302]);
        await logOut(owner, browser.indexedDB, { clearStorage: true });
        slow.release();
        await submit;

        expect(await keyState(cold)).toEqual({ memory: false, persisted: false });
        expect(cold.window.location.href).toBe("/");
        expect(cold.loginPosts).toEqual([302]);
        expect(cold.form.submit).not.toHaveBeenCalled();
    }, 10000);

    it("starts the login only after the preparation, so a wipe during it precedes the attempt", async () => {
        const browser = newBrowser();
        const owner = await loginPage(browser);
        await owner.signIn();
        const slow = slowIndexedDB(browser.indexedDB);
        slow.hold();
        const cold = await loginPage(browser, { indexedDB: slow.factory, cold: true });

        cold.keydown();
        const submit = cold.signIn();
        await wait(1100);
        expect(cold.loginPosts).toEqual([]); // nothing sent while the marker is unknown
        await logOut(owner, browser.indexedDB, { clearStorage: true });
        slow.release();
        await submit;

        expect(cold.loginPosts).toEqual([302]);
        expect(await keyState(cold)).toEqual({ memory: true, persisted: true });
    });

    it("waits for the first marker read when the page has not read it yet", async () => {
        const browser = newBrowser();
        const slow = slowIndexedDB(browser.indexedDB);
        slow.hold();
        const cold = await loginPage(browser, { indexedDB: slow.factory, cold: true });

        const submit = cold.signIn(); // no key press, no click: e.g. a password manager
        await wait(100);
        expect(cold.loginPosts).toEqual([]);
        slow.release();
        await submit;

        expect(cold.loginPosts).toEqual([302]);
        expect(await keyState(cold)).toEqual({ memory: true, persisted: true });
    });

    it("gets a key when slow storage answers after more than a second", async () => {
        const browser = newBrowser();
        const owner = await loginPage(browser);
        await owner.signIn();
        const slow = slowIndexedDB(browser.indexedDB);
        const waiting = await loginPage(browser, { indexedDB: slow.factory });
        await owner.session.wipeOfflineState();

        slow.hold();
        waiting.keydown();
        const submit = waiting.signIn();
        await wait(1100);
        slow.release();
        await submit;

        expect(await keyState(waiting)).toEqual({ memory: true, persisted: true });
        expect(waiting.loginPosts).toEqual([302]);
    });
});

describe("databases of earlier releases", () => {
    it("rejects a derivation when an earlier release wipes, clears and recreates the database", async () => {
        const browser = newBrowser();
        await legacyDatabase(browser.indexedDB);
        const waiting = await loginPage(browser);

        const pending = await heldDerivation(waiting);
        await legacyWipe(browser.indexedDB);
        await clearSiteData(browser.indexedDB);
        await legacyDatabase(browser.indexedDB);
        pending.resume();

        expect(await pending.result).toBe("NoSessionKeyError");
        expect(await keyState(waiting)).toEqual({ memory: false, persisted: false });
    });

    it("derives a new key after the first password change on such a database", async () => {
        const browser = newBrowser();
        await legacyDatabase(browser.indexedDB);
        const page = await loginPage(browser, { formId: "password-change-form" });
        await page.session.deriveSessionKey(PASSWORD, SALT);
        expect(page.session.hasSessionKey()).toBe(true);

        page.form.requestSubmit();
        await vi.waitFor(() => expect(page.window.location.href).toBe("/"));

        expect(await keyState(page)).toEqual({ memory: true, persisted: true });
        expect(page.form.submit).not.toHaveBeenCalled();
    });

    it("signs in on such a database, also after a wipe by an earlier release", async () => {
        const browser = newBrowser();
        await legacyDatabase(browser.indexedDB);
        await legacyWipe(browser.indexedDB);
        const page = await loginPage(browser);

        await page.signIn();

        expect(await keyState(page)).toEqual({ memory: true, persisted: true });
    });
});
