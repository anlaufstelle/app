// Refs #1948: sw-register.js stoesst die Precache-Selbstheilung an — einmal
// pro Seitenladen ein CHECK_PRECACHE an den AKTIVEN Worker. Beim
// Erst-Install (registration.active noch null) passiert bewusst nichts:
// install hat den Precache gerade selbst befuellt.
//
// sw-register.js ist eine IIFE — jeder Fall braucht einen frischen
// Modul-Import (vi.resetModules) mit vorab gesetzten Stubs (Muster
// sw-boot-purge-gate.test.mjs).
import { describe, it, expect, afterEach, vi } from "vitest";

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
            addEventListener: () => {},
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
    delete window.crypto_session;
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
    expect(postMessage).toHaveBeenCalledWith({ type: "SET_SHELL_LANGUAGE", language: "en" });
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
    expect(postMessage).toHaveBeenCalledWith({ type: "SET_SHELL_LANGUAGE", language: "de" });
    document.body.innerHTML = "";
});
