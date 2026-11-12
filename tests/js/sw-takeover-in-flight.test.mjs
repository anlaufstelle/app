// Refs #2730: after a worker takeover the page reloads itself only when the
// reload cannot cut a request short. A reload aborts a navigation that is under
// way (native form POST, link, location change) and a running HTMX write: the
// server completes the request, but the browser drops the answer together with
// a renewed session cookie. While one of them runs the page shows the update
// prompt instead (same treatment as unsaved input or an unsent offline queue).
//
// sw-register.js is an IIFE and reads the controller once while loading, so
// every case imports a fresh module (pattern: sw-controller-loss.test.mjs).
import { describe, it, expect, afterEach, vi } from "vitest";

function installServiceWorkerStub() {
    const listeners = {};
    const registration = {
        waiting: null,
        addEventListener: () => {},
    };
    const sw = {
        controller: { scriptURL: "http://localhost/sw.js" },
        addEventListener: (type, cb) => {
            (listeners[type] = listeners[type] || []).push(cb);
        },
        register: () => Promise.resolve(registration),
    };
    Object.defineProperty(window.navigator, "serviceWorker", { configurable: true, value: sw });
    return {
        takeOver() {
            sw.controller = { scriptURL: "http://localhost/sw.js?v=2" };
            (listeners.controllerchange || []).forEach((cb) => cb({ type: "controllerchange" }));
        },
    };
}

// Every module instance adds listeners to document/window; remove them again so
// an earlier instance does not answer for the next case.
let _added = [];

function removeListeners() {
    _added.forEach(([target, type, cb, opts]) => target.removeEventListener(type, cb, opts));
    _added = [];
}

function recordListeners(target) {
    const original = target.addEventListener;
    const own = Object.prototype.hasOwnProperty.call(target, "addEventListener");
    target.addEventListener = function (type, cb, opts) {
        _added.push([target, type, cb, opts]);
        return original.call(this, type, cb, opts);
    };
    return () => {
        if (own) target.addEventListener = original;
        else delete target.addEventListener;
    };
}

async function freshLoad({ navigation } = {}) {
    removeListeners();
    vi.resetModules();
    document.body.innerHTML = "";
    const reload = vi.fn();
    Object.defineProperty(window, "location", {
        configurable: true,
        writable: true,
        value: { reload, href: "http://localhost/mfa/settings/", pathname: "/mfa/settings/" },
    });
    if (navigation) {
        Object.defineProperty(window, "navigation", { configurable: true, value: navigation });
    }
    const harness = installServiceWorkerStub();
    const restore = [document, window].map(recordListeners);
    try {
        await import("../../src/static/js/sw-register.js");
    } finally {
        restore.forEach((undo) => undo());
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
    return { ...harness, reload };
}

function settle() {
    return new Promise((resolve) => setTimeout(resolve, 10));
}

function updatePrompt() {
    return document.getElementById("sw-update-toast");
}

function nativeForm() {
    const form = document.createElement("form");
    form.setAttribute("method", "post");
    form.setAttribute("action", "/mfa/passkeys/1/delete/");
    document.body.appendChild(form);
    return form;
}

function submit(form) {
    const event = new Event("submit", { bubbles: true, cancelable: true });
    form.dispatchEvent(event);
    return event;
}

function htmxEvent(type, verb, xhr) {
    document.body.dispatchEvent(
        new CustomEvent(type, { bubbles: true, detail: { xhr, requestConfig: { verb } } })
    );
}

function fakeNavigation() {
    const listeners = [];
    return {
        addEventListener: (type, cb) => {
            if (type === "navigate") listeners.push(cb);
        },
        navigate(sameDocument) {
            listeners.forEach((cb) => cb({ destination: { sameDocument }, downloadRequest: null }));
        },
    };
}

afterEach(() => {
    removeListeners();
    delete window.navigation;
    delete window.offlineQueue;
    document.body.innerHTML = "";
    vi.useRealTimers();
    vi.restoreAllMocks();
});

describe("takeover reload while a navigation is under way", () => {
    it("does not reload while a native form POST is under way and shows the prompt", async () => {
        const h = await freshLoad();
        submit(nativeForm());

        h.takeOver();
        await settle();

        expect(h.reload).not.toHaveBeenCalled();
        expect(updatePrompt()).not.toBeNull();
    });

    it("still reloads when a script cancelled the submit (it never navigated)", async () => {
        const h = await freshLoad();
        const form = nativeForm();
        form.addEventListener("submit", (event) => event.preventDefault());
        submit(form);

        h.takeOver();
        await settle();

        expect(h.reload).toHaveBeenCalledTimes(1);
    });

    it("does not reload when the submit starts while the offline queue is being counted", async () => {
        let release;
        window.offlineQueue = {
            getQueueCount: () => new Promise((resolve) => { release = () => resolve(0); }),
        };
        const h = await freshLoad();

        h.takeOver();
        submit(nativeForm());
        release();
        await settle();

        expect(h.reload).not.toHaveBeenCalled();
        expect(updatePrompt()).not.toBeNull();
    });

    it("does not reload while a cross-document navigation reported by the Navigation API is under way", async () => {
        const navigation = fakeNavigation();
        const h = await freshLoad({ navigation });
        navigation.navigate(false);

        h.takeOver();
        await settle();

        expect(h.reload).not.toHaveBeenCalled();
        expect(updatePrompt()).not.toBeNull();
    });

    it("still reloads after a same-document navigation (history entry, no request)", async () => {
        const navigation = fakeNavigation();
        const h = await freshLoad({ navigation });
        navigation.navigate(true);

        h.takeOver();
        await settle();

        expect(h.reload).toHaveBeenCalledTimes(1);
    });

    it("reloads again once the navigation window has passed (the page stayed)", async () => {
        const h = await freshLoad();
        const start = Date.now();
        submit(nativeForm());
        vi.spyOn(Date, "now").mockReturnValue(start + 60000);

        h.takeOver();
        await settle();

        expect(h.reload).toHaveBeenCalledTimes(1);
    });
});

describe("takeover reload while an HTMX write is under way", () => {
    it("does not reload while an HTMX POST is running and shows the prompt", async () => {
        const h = await freshLoad();
        htmxEvent("htmx:beforeSend", "post", { readyState: 1 });

        h.takeOver();
        await settle();

        expect(h.reload).not.toHaveBeenCalled();
        expect(updatePrompt()).not.toBeNull();
    });

    it("reloads once the HTMX POST has finished", async () => {
        const h = await freshLoad();
        const xhr = { readyState: 1 };
        htmxEvent("htmx:beforeSend", "post", xhr);
        xhr.readyState = 4;
        htmxEvent("htmx:afterRequest", "post", xhr);

        h.takeOver();
        await settle();

        expect(h.reload).toHaveBeenCalledTimes(1);
    });

    it("does not count a finished request whose afterRequest event never arrived", async () => {
        const h = await freshLoad();
        const xhr = { readyState: 1 };
        htmxEvent("htmx:beforeSend", "delete", xhr);
        xhr.readyState = 4;

        h.takeOver();
        await settle();

        expect(h.reload).toHaveBeenCalledTimes(1);
    });

    it("an HTMX GET does not hold back the reload", async () => {
        const h = await freshLoad();
        htmxEvent("htmx:beforeSend", "get", { readyState: 1 });

        h.takeOver();
        await settle();

        expect(h.reload).toHaveBeenCalledTimes(1);
    });
});
