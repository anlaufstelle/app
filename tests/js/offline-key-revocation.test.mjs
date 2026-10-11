// Refs #2528.
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

async function newTab(indexedDB, mode) {
    const errorNode = { hidden: true };
    const demoNode = { getAttribute: () => PASSWORD, removeAttribute() {} };
    let submit;
    const form = {
        action: "/login/", submit: vi.fn(),
        addEventListener: (_type, handler) => { submit = handler; },
    };
    const document = {
        body: { dataset: { offlineKeyMaxAge: "3600", sessionAge: "7200" } },
        querySelectorAll: () => [], addEventListener() {}, readyState: "complete",
        getElementById: (id) => mode === "login" && id === "login-form" ? form : null,
        querySelector(selector) {
            if (selector === "[data-demo-offline-password]" && mode === "demo") return demoNode;
            if (selector === "[data-demo-offline-error]") return errorNode;
            return null;
        },
    };
    const subtle = {
        importKey: webcrypto.subtle.importKey.bind(webcrypto.subtle),
        deriveKey: webcrypto.subtle.deriveKey.bind(webcrypto.subtle),
    };
    const window = {
        crypto: { subtle }, indexedDB, addEventListener() {}, setInterval() {},
        getCsrfToken: () => "csrf", location: { pathname: "/login/" },
        offlineStore: { revalidateCachedFacility: vi.fn().mockResolvedValue() },
    };
    const fetch = vi.fn();
    const context = createContext({
        window, document, indexedDB, TextEncoder, TextDecoder, atob, btoa, console, fetch,
        FormData: class { get() { return PASSWORD; } },
    });
    runInContext(cryptoSource, context);
    const session = window.crypto_session;
    await session.ready();
    return {
        window, session, subtle, fetch, form, errorNode,
        bootstrap() {
            runInContext(bootstrapSource, context);
            if (mode === "login") return submit({ preventDefault() {} });
        },
    };
}

async function assertCleared(tab) {
    expect(await tab.session.hasPersistedSessionKey()).toBe(false);
    await tab.session.awaitReady();
    expect(tab.session.hasSessionKey()).toBe(false);
}

describe("pending derivation across tabs", () => {
    it.each(["local", "broadcast", "no-broadcast"])("rejects a pending derivation after %s clear", async (mode) => {
        const db = new IDBFactory();
        const a = await newTab(db);
        await a.session.deriveSessionKey(PASSWORD, SALT);
        const b = mode === "local" ? a : await newTab(db);
        const started = deferred();
        const resume = deferred();
        const original = b.subtle.deriveKey;
        b.subtle.deriveKey = async (...args) => {
            const key = await original(...args);
            started.resolve();
            await resume.promise;
            return key;
        };
        const pending = b.session.deriveSessionKey(PASSWORD, SALT)
            .then(() => "derived", (error) => error.name);
        await started.promise;
        expect(await a.session.clearSessionKey()).toEqual({ persisted: true });
        expect(await a.session.hasPersistedSessionKey()).toBe(false);
        if (mode === "broadcast") {
            let delivery;
            b.window.syncOrchestrator = { runExclusive: (fn) => { delivery = fn(); return delivery; } };
            b.window.syncOrchestratorPending.at(-1)({ type: "key-cleared" });
            await delivery;
            expect(b.session.hasSessionKey()).toBe(false);
        }
        resume.resolve();
        expect(await pending).toBe("NoSessionKeyError");
        expect(b.session.hasSessionKey()).toBe(false);
        await assertCleared(a);
        await assertCleared(b);
        // A deliberate new login after revocation must remain possible.
        b.subtle.deriveKey = original;
        await b.session.deriveSessionKey(PASSWORD, SALT);
        expect(b.session.hasSessionKey()).toBe(true);
        expect(await a.session.hasPersistedSessionKey()).toBe(true);
    });
});

describe("bootstrap revocation before derivation starts", () => {
    it.each(["demo", "login"])("rejects a delayed %s salt response after clear", async (mode) => {
        const db = new IDBFactory();
        const tab = await newTab(db, mode);
        await tab.session.deriveSessionKey(PASSWORD, SALT);
        const saltResponse = deferred();
        tab.fetch.mockImplementation(async (url) => {
            if (url === "/login/") return { status: 302 };
            if (url === "/") return { ok: true, text: async () => '<meta name="csrf-token" content="fresh">' };
            if (url === "/auth/offline-key-salt/") return saltResponse.promise;
            throw new Error("Unexpected URL");
        });
        const completion = tab.bootstrap();
        await vi.waitFor(() => expect(tab.fetch.mock.calls.some(([url]) => url === "/auth/offline-key-salt/")).toBe(true));
        tab.window.__logoutInFlight = Date.now();
        expect(await tab.session.clearSessionKey()).toEqual({ persisted: true });
        expect(await tab.session.hasPersistedSessionKey()).toBe(false);
        saltResponse.resolve({ ok: true, json: async () => ({ salt: SALT, offline_key_max_age: 3600, home_url: "/" }) });
        if (completion) await completion;
        else await vi.waitFor(async () => {
            const finished = mode === "demo" ? !tab.errorNode.hidden : tab.form.submit.mock.calls.length > 0;
            // Wait for either outcome so the vulnerable baseline fails on behavior.
            expect(finished || tab.window.offlineStore.revalidateCachedFacility.mock.calls.length > 0).toBe(true);
        });
        expect(tab.session.hasSessionKey()).toBe(false);
        await assertCleared(tab);
        expect(tab.window.offlineStore.revalidateCachedFacility).not.toHaveBeenCalled();
        expect(tab.window.location.href).toBeUndefined();
        expect(tab.form.submit).not.toHaveBeenCalled();
    });
});
