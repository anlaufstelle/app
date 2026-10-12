import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import Dexie from "dexie";
import { importOfflineStoreModules } from "./_offline_store_modules.mjs";

const PASSWORD = "public demo password";
const SALT = "c2FsdHNhbHRzYWx0";
const START = Date.parse("2026-10-10T08:00:00Z");
let store;
let now;

beforeAll(async () => {
    window.Dexie = globalThis.Dexie = Dexie;
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.ready();
    await importOfflineStoreModules();
    store = window.offlineStore;
});
beforeEach(async () => {
    vi.resetModules();
    now = START;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    document.body.innerHTML = '<meta name="csrf-token" content="rotated-token"><div data-demo-offline-password="public demo password"></div><div data-demo-offline-error hidden role="alert">Bootstrap failed</div>';
    document.body.dataset.sessionAge = "7200";
    document.body.dataset.offlineKeyMaxAge = "43200";
    await store.purgeAll();
    await window.crypto_session.clearSessionKey();
    vi.spyOn(store, "revalidateCachedFacility").mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
        ok: true, json: async () => ({ salt: SALT, home_url: "/", offline_key_max_age: 3600 }),
    }));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function bootstrap() {
    await import("../../src/static/js/auth-bootstrap.js");
}

describe("demo offline bootstrap", () => {
    it("fetches one audited salt, derives a usable key and warms facility metadata", async () => {
        await bootstrap();
        await vi.waitFor(() => expect(window.crypto_session.hasSessionKey()).toBe(true));
        expect(fetch).toHaveBeenCalledTimes(1);
        expect(fetch).toHaveBeenCalledWith("/auth/offline-key-salt/", expect.objectContaining({
            method: "POST", credentials: "same-origin",
            headers: expect.objectContaining({ "X-CSRFToken": "rotated-token", "X-Demo-Offline-Bootstrap": "1" }),
        }));
        await store.saveOfflineEdit({ pk: "demo-event", data: { note: "demo work" } });
        expect((await store.getOfflineEvent("demo-event")).data.note).toBe("demo work");
        expect(store.revalidateCachedFacility).toHaveBeenCalledTimes(1);
        expect(document.querySelector("[data-demo-offline-error]").hidden).toBe(true);
        expect(document.querySelector("[data-demo-offline-password]")).toBeNull();
        vi.resetModules();
        await bootstrap();
        expect(fetch).toHaveBeenCalledTimes(1);
    });

    it("honours the server lifetime and preserves encrypted work after expiration", async () => {
        await bootstrap();
        await vi.waitFor(() => expect(window.crypto_session.hasSessionKey()).toBe(true));
        await store.saveOfflineEdit({ pk: "demo-event", data: { note: "kept" } });
        now += 3600_000;
        expect(await store.getOfflineEvent("demo-event")).toBeNull();
        expect(window.crypto_session.hasSessionKey()).toBe(false);
        expect(await store.db.events.count()).toBe(1);
        await window.crypto_session.deriveSessionKey(PASSWORD, SALT);
        expect((await store.getOfflineEvent("demo-event")).data.note).toBe("kept");
    });

    it.each(["mismatch", "network", "invalid-policy"])("shows a visible error without resetting work on %s", async (failure) => {
        await window.crypto_session.deriveSessionKey(PASSWORD, SALT);
        await store.saveOfflineEdit({ pk: "previous-work", data: { note: "preserved" } });
        if (failure === "mismatch") fetch.mockResolvedValue({ ok: false, status: 403 });
        if (failure === "network") fetch.mockRejectedValue(new Error("Offline"));
        if (failure === "invalid-policy") fetch.mockResolvedValue({ ok: true, json: async () => ({ salt: SALT, offline_key_max_age: 0 }) });
        await bootstrap();
        await vi.waitFor(() => expect(document.querySelector("[data-demo-offline-error]").hidden).toBe(false));
        expect((await store.getOfflineEvent("previous-work")).data.note).toBe("preserved");
        expect(store.revalidateCachedFacility).not.toHaveBeenCalled();
    });

    it("does nothing when the server has not rendered demo data", async () => {
        document.querySelector("[data-demo-offline-password]").remove();
        await bootstrap();
        expect(fetch).not.toHaveBeenCalled();
        expect(window.crypto_session.hasSessionKey()).toBe(false);
    });

    it("shows the bootstrap error when WebCrypto is unsupported", async () => {
        document.body.insertAdjacentHTML("beforeend", '<div data-demo-offline-pending></div>');
        const cryptoApi = window.crypto_session;
        Object.defineProperty(window, "crypto_session", { configurable: true, value: { isSupported: () => false } });
        try {
            await bootstrap();
            expect(document.querySelector("[data-demo-offline-error]").hidden).toBe(false);
            expect(fetch).not.toHaveBeenCalled();
            expect(document.querySelector("[data-demo-offline-pending]").hidden).toBe(true);
        } finally {
            Object.defineProperty(window, "crypto_session", { configurable: true, value: cryptoApi });
        }
    });
});


describe("demo bootstrap interruption notice", () => {
    it("shows the recovery instruction on the next demo page without a key", async () => {
        document.querySelector("[data-demo-offline-password]").remove();
        await bootstrap();
        await vi.waitFor(() => expect(document.querySelector("[data-demo-offline-error]").hidden).toBe(false));
        expect(fetch).not.toHaveBeenCalled();
    });

    it("hides the waiting notice only after derivation completes", async () => {
        document.body.insertAdjacentHTML("beforeend", '<div data-demo-offline-pending></div>');
        let release;
        fetch.mockReturnValue(new Promise((resolve) => { release = resolve; }));
        await bootstrap();
        await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
        expect(document.querySelector("[data-demo-offline-pending]").hidden).toBe(false);
        release({ ok: true, json: async () => ({ salt: SALT, home_url: "/", offline_key_max_age: 3600 }) });
        await vi.waitFor(() => expect(window.crypto_session.hasSessionKey()).toBe(true));
        await vi.waitFor(() => expect(document.querySelector("[data-demo-offline-pending]").hidden).toBe(true));
    });
});


it("does not show a demo recovery error when a persisted key is restored", async () => {
    await window.crypto_session.deriveSessionKey(PASSWORD, SALT);
    document.querySelector("[data-demo-offline-password]").remove();
    await bootstrap();
    await window.crypto_session.ready();
    expect(document.querySelector("[data-demo-offline-error]").hidden).toBe(true);
    expect(fetch).not.toHaveBeenCalled();
});
