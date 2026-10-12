import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";
import { describe, expect, it } from "vitest";

const labels = {
    i18nOfflineClientAvailable: "Person is now available offline.",
    i18nOfflineKeyInactive: "Offline key is not active — please sign in again.",
    i18nOfflineStorageUnavailable: "Offline storage is unavailable.",
    i18nOfflineConflictChanged: "The server has changed again. Please review the conflict once more.",
};

function page(module) {
    const dom = new JSDOM("<body></body>", { url: "https://example.test/", runScripts: "outside-only" });
    const w = dom.window;
    Object.assign(w.document.body.dataset, labels);
    const factories = {};
    w.Alpine = { data: (name, build) => { factories[name] = build; } };
    w.eval(readFileSync(`src/static/js/${module}.js`, "utf8"));
    w.document.dispatchEvent(new w.Event("alpine:init"));
    return { dom, w, factories };
}

describe("translated offline feedback", () => {
    it.each(["client-row-offline", "offline-client-badge"])("uses delivered success and key-error labels in %s", async (module) => {
        const { dom, w, factories } = page(module);
        try {
            w.crypto_session = { isSupported: () => true };
            w.offlineClient = {
                takeClientOffline: async () => ({}), isClientOffline: async () => false,
            };
            const component = factories[module === "client-row-offline" ? "clientRowOffline" : "offlineClientBadge"]();
            let feedback;
            w.addEventListener("offline-feedback", (event) => { feedback = event.detail.text; });
            const message = () => module === "client-row-offline" ? feedback : component.message;
            await component.toggleOffline();
            expect(message()).toBe(labels.i18nOfflineClientAvailable);
            w.offlineClient.takeClientOffline = async () => { throw Object.assign(new Error("Missing key"), { name: "NoSessionKeyError" }); };
            await component.toggleOffline();
            expect(message()).toBe(labels.i18nOfflineKeyInactive);
        } finally { dom.window.close(); }
    });

    it("uses delivered storage and repeated-conflict labels", async () => {
        const { dom, w, factories } = page("conflict-resolver");
        try {
            const component = factories.conflictResolver();
            await component.load();
            expect(component.error).toBe(labels.i18nOfflineStorageUnavailable);
            component.load = () => {};
            component._markResult({ status: "conflict" });
            expect(component.error).toBe(labels.i18nOfflineConflictChanged);
        } finally { dom.window.close(); }
    });
});
