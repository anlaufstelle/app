import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const factories = {};
const MESSAGES = {
    copySuccess: "Codes kopiert.",
    copyError: "Kopieren nicht möglich. Bitte herunterladen oder drucken.",
};

beforeAll(async () => {
    window.Alpine = {
        data: vi.fn((name, factory) => {
            factories[name] = factory;
        }),
    };
    await import("../../src/static/js/alpine/auth.js");
    document.dispatchEvent(new Event("alpine:init"));
});

function mountComponent() {
    document.body.innerHTML = `
        <div id="root"><ul id="backup-codes-list">
            <li> aaaa-bbbb </li><li>cccc-dddd</li>
        </ul></div>`;
    const component = factories.backupCodesAcknowledge();
    const root = document.getElementById("root");
    root.dataset.copySuccess = MESSAGES.copySuccess;
    root.dataset.copyError = MESSAGES.copyError;
    component.$root = root;
    component.$el = document.createElement("button");
    return component;
}

function setClipboard(value) {
    Object.defineProperty(navigator, "clipboard", { value, configurable: true });
}

const unhandled = [];
const onUnhandled = (event) => unhandled.push(event);

beforeEach(() => {
    unhandled.length = 0;
    process.on("unhandledRejection", onUnhandled);
});

afterEach(() => {
    process.off("unhandledRejection", onUnhandled);
    setClipboard(undefined);
});

describe("backupCodesAcknowledge.copyCodes", () => {
    it("reports success only after writeText resolved", async () => {
        let resolveWrite;
        const writeText = vi.fn(
            () => new Promise((resolve) => {
                resolveWrite = resolve;
            }),
        );
        setClipboard({ writeText });
        const component = mountComponent();

        const pending = component.copyCodes();
        expect(writeText).toHaveBeenCalledWith("aaaa-bbbb\ncccc-dddd");
        expect(component.copyStatus).toBe("");
        expect(component.copyError).toBe("");

        resolveWrite();
        await pending;
        expect(component.copyStatus).toBe(MESSAGES.copySuccess);
        expect(component.copyError).toBe("");
    });

    it("shows an error and no success when the permission is denied", async () => {
        setClipboard({
            writeText: vi.fn(() => Promise.reject(new DOMException("denied", "NotAllowedError"))),
        });
        const component = mountComponent();

        await expect(component.copyCodes()).resolves.toBeUndefined();
        expect(component.copyStatus).toBe("");
        expect(component.copyError).toBe(MESSAGES.copyError);
        await new Promise((resolve) => setTimeout(resolve, 0));
        expect(unhandled).toEqual([]);
    });

    it("shows an error when the Clipboard API is missing", async () => {
        setClipboard(undefined);
        const component = mountComponent();

        await expect(component.copyCodes()).resolves.toBeUndefined();
        expect(component.copyStatus).toBe("");
        expect(component.copyError).toBe(MESSAGES.copyError);
    });

    it("shows an error when writeText throws synchronously", async () => {
        setClipboard({
            writeText: vi.fn(() => {
                throw new TypeError("not a function");
            }),
        });
        const component = mountComponent();

        await component.copyCodes();
        expect(component.copyStatus).toBe("");
        expect(component.copyError).toBe(MESSAGES.copyError);
    });

    it("clears an earlier error after a later successful copy", async () => {
        const writeText = vi
            .fn()
            .mockRejectedValueOnce(new Error("denied"))
            .mockResolvedValueOnce(undefined);
        setClipboard({ writeText });
        const component = mountComponent();

        await component.copyCodes();
        expect(component.copyError).toBe(MESSAGES.copyError);
        await component.copyCodes();
        expect(component.copyError).toBe("");
        expect(component.copyStatus).toBe(MESSAGES.copySuccess);
    });

    it("keeps the backup confirmation independent of the copy result", async () => {
        setClipboard({ writeText: vi.fn(() => Promise.reject(new Error("denied"))) });
        const component = mountComponent();

        await component.copyCodes();
        expect(component.notConfirmed).toBe(true);
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = true;
        component.$el = checkbox;
        component.syncConfirmed();
        expect(component.notConfirmed).toBe(false);
    });
});
