import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import Dexie from "dexie";
import { importOfflineStoreModules } from "./_offline_store_modules.mjs";

const SALT = "c2FsdHNhbHRzYWx0";

beforeAll(async () => {
    window.Dexie = globalThis.Dexie = Dexie;
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.ready();
    await importOfflineStoreModules();
});

let navigations;
let nativeSubmit;

beforeEach(() => {
    vi.resetModules();
    document.body.innerHTML = '<meta name="csrf-token" content="login-token">'
        + '<form id="login-form" method="post"><input name="username" value="miriam"><input name="password" value="pw"></form>';
    delete document.body.dataset.userId;
    vi.spyOn(window.offlineStore, "revalidateCachedFacility").mockResolvedValue({ ok: true });
    nativeSubmit = vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(() => {});
    // Record navigations instead of letting jsdom report "not implemented".
    navigations = [];
    vi.stubGlobal("location", {
        search: "",
        pathname: "/login/",
        set href(value) { navigations.push(value); },
        get href() { return "http://localhost/login/"; },
    });
    vi.spyOn(console, "warn").mockImplementation(() => {});
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

// ``crypto_session`` is frozen, so failures come from the real derivation:
// an undecodable salt makes it throw, a key wipe in between revokes the attempt.
function stubFetch(loginResponse, { salt = SALT, beforeSalt } = {}) {
    vi.stubGlobal("fetch", vi.fn(async (url, options = {}) => {
        if (options.method === "POST" && String(url).includes("/auth/offline-key-salt/")) {
            if (beforeSalt) await beforeSalt();
            return { ok: true, json: async () => ({ salt, home_url: "/", offline_key_max_age: 3600 }) };
        }
        if (options.method === "POST") return loginResponse;
        return { ok: true, text: async () => '<meta name="csrf-token" content="fresh">' };
    }));
}

async function submitLogin() {
    await import("../../src/static/js/auth-bootstrap.js");
    document.getElementById("login-form").dispatchEvent(new Event("submit", { cancelable: true }));
}

const accepted = { status: 302, type: "opaqueredirect", ok: false };

describe("login after the server accepted the credentials", () => {
    it("navigates home instead of re-posting when derivation throws after sign-in", async () => {
        stubFetch(accepted, { salt: "!!!" });
        await submitLogin();
        await vi.waitFor(() => expect(navigations).toEqual(["/"]));
        expect(nativeSubmit).not.toHaveBeenCalled();
        expect(console.warn).toHaveBeenCalled();
    });

    it("leaves the login page after sign-in even on NoSessionKeyError", async () => {
        // Another tab wipes the key while this login is deriving it.
        stubFetch(accepted, { beforeSalt: () => window.crypto_session.clearSessionKey() });
        await submitLogin();
        await vi.waitFor(() => expect(navigations).toEqual(["/"]));
        expect(nativeSubmit).not.toHaveBeenCalled();
    });

    it("does not re-post when the salt response is not JSON", async () => {
        vi.stubGlobal("fetch", vi.fn(async (url, options = {}) => {
            if (options.method === "POST" && String(url).includes("/auth/offline-key-salt/")) {
                return { ok: true, json: async () => { throw new SyntaxError("Unexpected token <"); } };
            }
            if (options.method === "POST") return accepted;
            return { ok: true, text: async () => '<meta name="csrf-token" content="fresh">' };
        }));
        await submitLogin();
        await vi.waitFor(() => expect(navigations).toEqual(["/"]));
        expect(nativeSubmit).not.toHaveBeenCalled();
    });
});

describe("password change after the server accepted the new password", () => {
    it("navigates home instead of re-posting the spent form when derivation fails", async () => {
        document.body.innerHTML = '<meta name="csrf-token" content="page-token">'
            + '<form id="password-change-form" method="post"><input name="new_password1" value="pw"></form>';
        vi.spyOn(window.offlineStore, "countUnsyncedEvents").mockResolvedValue(0);
        vi.spyOn(window.offlineStore, "purgeAll").mockResolvedValue();
        stubFetch(accepted, { salt: "!!!" });
        await import("../../src/static/js/auth-bootstrap.js");
        document.getElementById("password-change-form").dispatchEvent(new Event("submit", { cancelable: true }));
        await vi.waitFor(() => expect(navigations).toEqual(["/"]));
        expect(nativeSubmit).not.toHaveBeenCalled();
    });
});

describe("login before the server accepted the credentials", () => {
    it("still falls back to native submit when the server rejects the credentials", async () => {
        stubFetch({ status: 200, type: "basic", ok: true });
        await submitLogin();
        await vi.waitFor(() => expect(nativeSubmit).toHaveBeenCalledTimes(1));
        expect(fetch.mock.calls.some(([url]) => String(url).includes("/auth/offline-key-salt/"))).toBe(false);
        expect(navigations).toEqual([]);
    });

    it("still falls back to native submit when the login request itself fails", async () => {
        vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network"); }));
        await submitLogin();
        await vi.waitFor(() => expect(nativeSubmit).toHaveBeenCalledTimes(1));
        expect(navigations).toEqual([]);
    });
});
