// Refs #1948: sw-register.js stoesst die Precache-Selbstheilung an — einmal
// pro Seitenladen ein CHECK_PRECACHE an den AKTIVEN Worker. Beim
// Erst-Install (registration.active noch null) passiert bewusst nichts:
// install hat den Precache gerade selbst befuellt.
//
// sw-register.js ist eine IIFE — jeder Fall braucht einen frischen
// Modul-Import (vi.resetModules) mit vorab gesetzten Stubs (Muster
// sw-boot-purge-gate.test.mjs).
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

const listeners = [];
const workerListeners = new Map();
beforeEach(() => {
    // Each import represents a fresh page, including its event listeners.
    for (const target of [document, window]) {
        const add = target.addEventListener;
        vi.spyOn(target, "addEventListener").mockImplementation(function (type, callback, options) {
            listeners.push([target, type, callback, options]);
            return add.call(target, type, callback, options);
        });
    }
});

function installServiceWorkerStub(activeWorker) {
    const registration = {
        waiting: null,
        active: activeWorker,
        addEventListener: () => {},
    };
    Object.defineProperty(window.navigator, "serviceWorker", {
        configurable: true,
        value: {
            controller: null,
            addEventListener: (type, callback) => { workerListeners.set(type, callback); },
            register: () => Promise.resolve(registration),
        },
    });
}

async function freshLoad(activeWorker) {
    vi.resetModules();
    installServiceWorkerStub(activeWorker);
    window.crypto_session = { awaitReady: async () => {}, hasSessionKey: () => false };
    await import("../../src/static/js/sw-register.js");
    await new Promise((resolve) => setTimeout(resolve, 10));
}

afterEach(() => {
    for (const [target, type, callback, options] of listeners.splice(0)) target.removeEventListener(type, callback, options);
    delete window.crypto_session;
    workerListeners.clear();
    vi.restoreAllMocks();
});

describe("#1948 — CHECK_PRECACHE-Anstoss beim Seitenladen", () => {
    it("sendet CHECK_PRECACHE an den aktiven Worker (Bestandsgeraet)", async () => {
        const postMessage = vi.fn();
        await freshLoad({ postMessage });

        expect(postMessage).toHaveBeenCalledWith({ type: "CHECK_PRECACHE" });
        expect(postMessage).toHaveBeenCalledTimes(1);
    });

    it("Erst-Install (kein aktiver Worker): kein Send, kein Crash", async () => {
        await expect(freshLoad(null)).resolves.toBeUndefined();
    });
});


it("sends the authenticated page language to the active worker", async () => {
    document.documentElement.lang = "en";
    document.body.dataset.userId = "u1";
    const postMessage = vi.fn();
    await freshLoad({ postMessage });
    expect(postMessage).toHaveBeenCalledWith({ type: "SET_SHELL_LANGUAGE", language: "en", requestId: expect.any(String) });
    delete document.body.dataset.userId;
    document.documentElement.lang = "";
});


it("resets the public shell language when the account logs out", async () => {
    const postMessage = vi.fn();
    await freshLoad({ postMessage });
    navigator.serviceWorker.controller = { postMessage };
    document.body.innerHTML = '<form action="/logout/" method="post"></form>';
    const form = document.querySelector("form");
    vi.spyOn(form, "submit").mockImplementation(() => {});
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await vi.waitFor(() => expect(form.submit).toHaveBeenCalledOnce());
    expect(postMessage).toHaveBeenCalledWith({ type: "SET_SHELL_LANGUAGE", language: "de", requestId: expect.any(String) });
    document.body.innerHTML = "";
});

it("resets language through the active registration even without a controller", async () => {
    document.documentElement.lang = "en";
    document.body.dataset.userId = "u1";
    const postMessage = vi.fn();
    await freshLoad({ postMessage });
    postMessage.mockClear();
    document.body.innerHTML = '<form action="/logout/" method="post"></form>';
    const form = document.querySelector("form");
    vi.spyOn(form, "submit").mockImplementation(() => {});
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await vi.waitFor(() => expect(form.submit).toHaveBeenCalledOnce());
    expect(postMessage).toHaveBeenCalledWith({ type: "SET_SHELL_LANGUAGE", language: "de", requestId: expect.any(String) });
    document.body.innerHTML = ""; delete document.body.dataset.userId; document.documentElement.lang = "";
});

it("retries a failed logout language message on reconnection without reverting to the account language", async () => {
    document.documentElement.lang = "en";
    document.body.dataset.userId = "u1";
    const postMessage = vi.fn();
    await freshLoad({ postMessage });
    navigator.serviceWorker.controller = { postMessage };
    postMessage.mockClear(); postMessage.mockImplementationOnce(() => { throw new Error("Worker unavailable"); });
    document.body.innerHTML = '<form action="/logout/" method="post"></form>';
    const form = document.querySelector("form");
    vi.spyOn(form, "submit").mockImplementation(() => {});
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await vi.waitFor(() => expect(form.submit).toHaveBeenCalledOnce());
    window.dispatchEvent(new Event("online"));
    expect(postMessage).toHaveBeenLastCalledWith({ type: "SET_SHELL_LANGUAGE", language: "de", requestId: expect.any(String) });
    expect(postMessage).toHaveBeenCalledTimes(2);
    document.body.innerHTML = ""; delete document.body.dataset.userId; document.documentElement.lang = "";
});

it("retries an unacknowledged language message and stops only after durable confirmation", async () => {
    document.documentElement.lang = "en"; document.body.dataset.userId = "u1";
    const postMessage = vi.fn(); await freshLoad({ postMessage }); postMessage.mockClear();
    window.dispatchEvent(new Event("online"));
    expect(postMessage).toHaveBeenCalledExactlyOnceWith({ type: "SET_SHELL_LANGUAGE", language: "en", requestId: expect.any(String) });
    await workerListeners.get("message")({ data: { type: "SHELL_LANGUAGE_STORED", language: "de" } });
    window.dispatchEvent(new Event("online")); expect(postMessage).toHaveBeenCalledTimes(2);
    await workerListeners.get("message")({ data: { type: "SHELL_LANGUAGE_STORED", language: "en", requestId: postMessage.mock.calls.at(-1)[0].requestId } });
    window.dispatchEvent(new Event("online")); expect(postMessage).toHaveBeenCalledTimes(2);
    delete document.body.dataset.userId; document.documentElement.lang = "";
});

it("an acknowledgement for an older message cannot complete the current language request", async () => {
    document.documentElement.lang = "en"; document.body.dataset.userId = "u1";
    const postMessage = vi.fn(); await freshLoad({ postMessage }); postMessage.mockClear();
    await workerListeners.get("message")({ data: { type: "SHELL_LANGUAGE_STORED", language: "en", requestId: "older-request" } });
    window.dispatchEvent(new Event("online"));
    expect(postMessage).toHaveBeenCalledOnce();
    delete document.body.dataset.userId; document.documentElement.lang = "";
});
