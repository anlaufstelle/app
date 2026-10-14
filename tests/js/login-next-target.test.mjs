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
beforeEach(() => {
    vi.resetModules();
    document.body.innerHTML = '<meta name="csrf-token" content="login-token">'
        + '<form id="login-form" method="post"><input name="username" value="miriam"><input name="password" value="pw"></form>';
    delete document.body.dataset.userId;
    vi.spyOn(window.offlineStore, "revalidateCachedFacility").mockResolvedValue({ ok: true });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); window.history.replaceState({}, "", "/"); });

async function submitLogin(search, homeUrl) {
    window.history.replaceState({}, "", `/login/${search}`);
    const calls = [];
    vi.stubGlobal("fetch", vi.fn(async (url, options = {}) => {
        calls.push([String(url), options.method || "GET"]);
        if (options.method === "POST" && String(url).includes("/auth/offline-key-salt/")) {
            return { ok: true, json: async () => ({ salt: SALT, home_url: homeUrl, offline_key_max_age: 3600 }) };
        }
        if (options.method === "POST") return { status: 302, type: "opaqueredirect", ok: false };
        return { ok: true, text: async () => '<meta name="csrf-token" content="fresh">' };
    }));
    await import("../../src/static/js/auth-bootstrap.js");
    document.getElementById("login-form").dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(calls.some(([url]) => url.includes("/auth/offline-key-salt/"))).toBe(true));
    return calls;
}

describe("login keeps the requested target", () => {
    it("passes the encoded next target to the salt endpoint", async () => {
        const calls = await submitLogin("?next=%2Fevents%2Fnew%2F%3Fclient%3D1", "/events/new/?client=1");
        const salt = calls.find(([url]) => url.includes("/auth/offline-key-salt/"));
        expect(salt[0]).toBe("/auth/offline-key-salt/?next=%2Fevents%2Fnew%2F%3Fclient%3D1");
        expect(salt[1]).toBe("POST");
    });

    it("asks without a target when the login page was opened directly", async () => {
        const calls = await submitLogin("", "/");
        const salt = calls.find(([url]) => url.includes("/auth/offline-key-salt/"));
        expect(salt[0]).toBe("/auth/offline-key-salt/");
    });
});
