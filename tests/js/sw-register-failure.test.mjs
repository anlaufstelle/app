// Refs #2630: a failed service worker registration must be handled, exposed as
// a small capability state and must not let the badge promise offline use.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listeners = [];
const unhandled = [];
const onUnhandled = (reason) => unhandled.push(reason);
const flush = () => new Promise((resolve) => setTimeout(resolve, 10));

beforeEach(() => {
    unhandled.length = 0;
    process.on("unhandledRejection", onUnhandled);
    vi.spyOn(console, "warn").mockImplementation(() => {});
    for (const target of [document, window]) {
        const add = target.addEventListener;
        vi.spyOn(target, "addEventListener").mockImplementation(function (type, callback, options) {
            listeners.push([target, type, callback, options]);
            return add.call(target, type, callback, options);
        });
    }
});

afterEach(() => {
    process.off("unhandledRejection", onUnhandled);
    for (const [target, type, callback, options] of listeners.splice(0)) target.removeEventListener(type, callback, options);
    delete window.crypto_session;
    delete window.offlineWorker;
    vi.restoreAllMocks();
});

function workerStub({ register, controller = null, getRegistration }) {
    const containerListeners = new Map();
    Object.defineProperty(window.navigator, "serviceWorker", {
        configurable: true,
        value: {
            controller,
            addEventListener: (type, callback) => containerListeners.set(type, callback),
            register,
            getRegistration: getRegistration || (async () => undefined),
        },
    });
    return containerListeners;
}

async function freshLoad() {
    vi.resetModules();
    window.crypto_session = { awaitReady: async () => {}, hasSessionKey: () => false };
    await import("../../src/static/js/sw-register.js");
    await flush();
}

function activeRegistration() {
    return { waiting: null, active: { postMessage: vi.fn() }, installing: null, addEventListener: () => {} };
}

function installingRegistration() {
    const worker = { state: "installing", listeners: {}, addEventListener(type, cb) { this.listeners[type] = cb; } };
    return { registration: { waiting: null, active: null, installing: worker, addEventListener: () => {} }, worker };
}

describe("worker capability state", () => {
    it("reports a rejected first registration as failed without an unhandled rejection", async () => {
        workerStub({ register: () => Promise.reject(new Error("WorkerRegistrationFailed")) });
        const seen = [];
        window.addEventListener("offline-worker-status", (event) => seen.push(event.detail.status));
        await freshLoad();

        expect(unhandled).toEqual([]);
        expect(window.offlineWorker.status()).toBe("failed");
        expect(window.offlineWorker.isUnavailable()).toBe(true);
        expect(seen).toContain("failed");
    });

    it("keeps an already controlling worker usable when only the registration fails", async () => {
        workerStub({ register: () => Promise.reject(new Error("update check failed")), controller: {} });
        await freshLoad();

        expect(unhandled).toEqual([]);
        expect(window.offlineWorker.status()).toBe("active");
        expect(window.offlineWorker.isUnavailable()).toBe(false);
    });

    it("keeps an existing active registration usable when register fails", async () => {
        workerStub({
            register: () => Promise.reject(new Error("update check failed")),
            getRegistration: async () => activeRegistration(),
        });
        await freshLoad();

        expect(window.offlineWorker.status()).toBe("active");
    });

    it("does not treat a running first installation as a failure", async () => {
        const { registration, worker } = installingRegistration();
        workerStub({ register: () => Promise.resolve(registration) });
        await freshLoad();

        expect(window.offlineWorker.status()).toBe("installing");
        expect(window.offlineWorker.isUnavailable()).toBe(false);

        worker.state = "activated";
        worker.listeners.statechange();
        expect(window.offlineWorker.status()).toBe("active");
    });

    it("reports a first installation that ends redundant as failed", async () => {
        const { registration, worker } = installingRegistration();
        workerStub({ register: () => Promise.resolve(registration) });
        await freshLoad();

        worker.state = "redundant";
        worker.listeners.statechange();
        expect(window.offlineWorker.status()).toBe("failed");
    });

    it("reports an active worker after a normal registration", async () => {
        workerStub({ register: () => Promise.resolve(activeRegistration()) });
        await freshLoad();

        expect(window.offlineWorker.status()).toBe("active");
    });

    it("retries the registration when the connection returns and recovers", async () => {
        const register = vi
            .fn()
            .mockRejectedValueOnce(new Error("WorkerRegistrationFailed"))
            .mockResolvedValue(activeRegistration());
        workerStub({ register });
        await freshLoad();
        expect(window.offlineWorker.status()).toBe("failed");

        window.dispatchEvent(new Event("online"));
        await flush();

        expect(register).toHaveBeenCalledTimes(2);
        expect(window.offlineWorker.status()).toBe("active");
    });

    it("does not retry while the state is not failed", async () => {
        const register = vi.fn().mockResolvedValue(activeRegistration());
        workerStub({ register });
        await freshLoad();

        window.dispatchEvent(new Event("online"));
        await flush();

        expect(register).toHaveBeenCalledTimes(1);
    });
});

describe("take-offline message with an unavailable worker", () => {
    const factories = {};

    async function loadBadge() {
        vi.resetModules();
        window.Alpine = { data: (name, factory) => { factories[name] = factory; } };
        window.offlineFormFields = { formatTs: () => "" };
        await import("../../src/static/js/offline-client-badge.js");
        document.dispatchEvent(new Event("alpine:init"));
        const badge = factories.offlineClientBadge();
        badge.$el = document.createElement("div");
        badge._pk = "pk-1";
        window.offlineClient = {
            takeClientOffline: vi.fn(async () => ({ survivingEdits: 0 })),
            isClientOffline: vi.fn(async () => true),
            getOfflineClient: vi.fn(async () => ({})),
        };
        return badge;
    }

    afterEach(() => {
        delete window.offlineClient;
        delete window.offlineFormFields;
        delete window.offlineWorker;
        document.body.removeAttribute("data-i18n-offline-worker-unavailable");
    });

    it("does not claim offline availability when the worker is unavailable", async () => {
        document.body.dataset.i18nOfflineWorkerUnavailable = "Saved locally, offline pages unavailable.";
        const badge = await loadBadge();
        window.offlineWorker = { isUnavailable: () => true, status: () => "failed" };

        await badge.toggleOffline();

        expect(window.offlineClient.takeClientOffline).toHaveBeenCalledOnce();
        expect(badge.message).toBe("Saved locally, offline pages unavailable.");
        expect(badge.message).not.toContain("jetzt offline verfügbar");
        expect(badge.messageType).toBe("error");
        // The stored bundle is kept and still reported as local.
        expect(badge.isOffline).toBe(true);
    });

    it("keeps the normal message while the worker is installing or active", async () => {
        const badge = await loadBadge();
        window.offlineWorker = { isUnavailable: () => false, status: () => "installing" };

        await badge.toggleOffline();

        expect(badge.message).toContain("offline verfügbar");
        expect(badge.messageType).toBe("info");
    });

    it("keeps the normal message when no worker state is published", async () => {
        const badge = await loadBadge();

        await badge.toggleOffline();

        expect(badge.message).toContain("offline verfügbar");
    });

    it("applies the same message rule in the client list row toggle", async () => {
        vi.resetModules();
        window.Alpine = { data: (name, factory) => { factories[name] = factory; } };
        await import("../../src/static/js/client-row-offline.js");
        document.dispatchEvent(new Event("alpine:init"));
        document.body.dataset.i18nOfflineWorkerUnavailable = "Saved locally, offline pages unavailable.";
        window.offlineClient = {
            takeClientOffline: vi.fn(async () => ({ survivingEdits: 0 })),
            isClientOffline: vi.fn(async () => true),
        };
        const feedback = [];
        const record = (event) => feedback.push(event.detail);
        window.addEventListener("offline-feedback", record);
        const row = factories.clientRowOffline();
        row._pk = "pk-1";

        window.offlineWorker = { isUnavailable: () => true };
        await row.toggleOffline();
        row.isOffline = false;
        window.offlineWorker = { isUnavailable: () => false };
        await row.toggleOffline();
        window.removeEventListener("offline-feedback", record);

        expect(feedback[0]).toEqual({ text: "Saved locally, offline pages unavailable.", type: "error" });
        expect(feedback[1].type).toBe("info");
        expect(feedback[1].text).toContain("offline verfügbar");
    });
});
