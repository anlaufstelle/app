import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { JSDOM, VirtualConsole } from "jsdom";
import { beforeAll, describe, expect, it, vi } from "vitest";

const slugs = ["selection", "constructor", "__proto__", "toString", "hasOwnProperty", "valueOf"];
let templates;

beforeAll(() => {
    // Render the shipped Django templates; no database or HTTP server is needed.
    templates = JSON.parse(execFileSync(".venv/bin/python", ["-c", `
import json
import django
django.setup()
from django.template.loader import render_to_string
print(json.dumps({name: render_to_string(name, {'event_pk': 'selection-event'}) for name in [
    'core/events/conflict_review.html',
    'core/clients/offline_detail.html',
    'core/clients/_offline_event_fields.html',
]}))
`], {
        encoding: "utf8",
        env: { ...process.env, PYTHONPATH: "src", DJANGO_SETTINGS_MODULE: "anlaufstelle.settings.test" },
    }));
});

function findInTemplates(root, selector) {
    const found = root.querySelector(selector);
    if (found) return found;
    for (const template of root.querySelectorAll("template")) {
        const nested = findInTemplates(template.content, selector);
        if (nested) return nested;
    }
    return null;
}

function createPage(html, modules) {
    const errors = [];
    const virtualConsole = new VirtualConsole();
    virtualConsole.on("warn", (...args) => errors.push(String(args[0])));
    virtualConsole.on("error", (...args) => errors.push(String(args[0])));
    const dom = new JSDOM(html, { runScripts: "outside-only", virtualConsole });
    dom.window.addEventListener("error", (event) => {
        errors.push(event.error?.message || event.message);
        event.preventDefault();
    });
    for (const module of ["offline-form-fields.js", ...modules]) {
        dom.window.eval(readFileSync(`src/static/js/${module}`, "utf8"));
    }
    return { dom, win: dom.window, errors };
}

async function startAlpine(win) {
    win.eval(readFileSync("src/static/js/alpine-csp.min.js", "utf8"));
    await vi.waitFor(() => expect(win.Alpine).toBeDefined());
    await win.Alpine.nextTick();
}

describe("Selection changes through the shipped Alpine CSP build", () => {
    it.each(slugs)("replays the server array selected in the conflict template for %s", async (slug) => {
        const parsed = new JSDOM(templates["core/events/conflict_review.html"]);
        const html = parsed.window.document.querySelector('[data-testid="conflict-resolver-view"]').outerHTML;
        parsed.window.close();
        const { dom, win, errors } = createPage(html, ["conflict-resolver.js"]);
        try {
            let record = {
                pk: "selection-event", localStatus: "conflict",
                data: {
                    formData: { [slug]: ["local-option"] },
                    serverState: { data_json: { [slug]: ["server-option"] }, updated_at: "fresh-token" },
                },
            };
            win.offlineStore = {
                getOfflineEvent: vi.fn(async () => structuredClone(record)),
                saveOfflineEdit: vi.fn(async (saved) => { record = JSON.parse(JSON.stringify(saved)); }),
                applyClientRevocations: vi.fn(async () => ({ status: "ok" })),
                isRevocationPending: vi.fn(async () => false),
            };
            win.offlineEdit = { replayModifiedEvent: vi.fn(async () => ({ status: "synced" })) };
            win.syncOrchestrator = { runExclusive: vi.fn(async (fn) => fn()) };
            await startAlpine(win);
            await vi.waitFor(() => expect(win.document.querySelector('[data-testid="conflict-choice"]')).not.toBeNull());
            const select = win.document.querySelector('[data-testid="conflict-choice"]');
            select.value = "server";
            select.dispatchEvent(new win.Event("change", { bubbles: true }));
            await win.Alpine.nextTick();
            expect(select.value).toBe("server");
            win.document.querySelector('[data-testid="conflict-merge"]').click();
            await vi.waitFor(() => expect(win.offlineEdit.replayModifiedEvent).toHaveBeenCalledOnce());
            const replayed = win.offlineEdit.replayModifiedEvent.mock.calls[0][0];
            expect(JSON.parse(JSON.stringify(replayed.data.formData))).toEqual({ [slug]: ["server-option"] });
            expect(Array.isArray(replayed.data.formData[slug])).toBe(true);
            expect(replayed.data.expectedUpdatedAt).toBe("fresh-token");
            expect(errors).toEqual([]);
        } finally {
            dom.window.close();
        }
    });

    it.each(["conflict-keep-local", "conflict-merge"])("keeps %s usable with the waiting status until a fresh retry succeeds", async (buttonId) => {
        const parsed = new JSDOM(templates["core/events/conflict_review.html"]);
        const html = parsed.window.document.querySelector('[data-testid="conflict-resolver-view"]').outerHTML;
        parsed.window.close();
        const { dom, win, errors } = createPage(html, ["conflict-resolver.js"]);
        try {
            let record = {
                pk: "selection-event", localStatus: "conflict",
                data: {
                    formData: { selection: ["local-option"] },
                    serverState: { data_json: { selection: ["server-option"] }, updated_at: "fresh-token" },
                },
            };
            win.offlineStore = {
                getOfflineEvent: vi.fn(async () => structuredClone(record)),
                saveOfflineEdit: vi.fn(async (saved) => { record = JSON.parse(JSON.stringify(saved)); }),
                applyClientRevocations: vi.fn(async () => ({ status: "error" })),
                isRevocationPending: vi.fn(async () => false),
            };
            win.offlineEdit = { replayModifiedEvent: vi.fn(async () => ({ status: "synced" })) };
            win.syncOrchestrator = { runExclusive: vi.fn(async (fn) => fn()) };
            await startAlpine(win);
            await vi.waitFor(() => expect(win.document.querySelector('[data-testid="conflict-choice"]')).not.toBeNull());
            const button = win.document.querySelector(`[data-testid="${buttonId}"]`);
            button.click();
            await vi.waitFor(() => {
                const status = win.document.querySelector('[data-testid="conflict-status"]');
                expect(status.textContent).toBe("Synchronisation wartet auf Widerrufsprüfung");
                expect(status.style.display).not.toBe("none");
                expect(button.disabled).toBe(false);
            });
            expect(win.document.querySelector('[data-testid="conflict-choice"]')).not.toBeNull();
            expect(win.document.querySelector('[data-testid="conflict-error"]')).toBeNull();
            expect(win.offlineEdit.replayModifiedEvent).not.toHaveBeenCalled();
            win.offlineStore.applyClientRevocations.mockResolvedValue({ status: "ok" });
            button.click();
            await vi.waitFor(() => expect(win.document.querySelector('[data-testid="conflict-resolved"]')).not.toBeNull());
            expect(win.offlineEdit.replayModifiedEvent).toHaveBeenCalledOnce();
            expect(win.offlineStore.applyClientRevocations).toHaveBeenCalledTimes(2);
            expect(errors).toEqual([]);
        } finally {
            dom.window.close();
        }
    });

    it.each(slugs)("saves the changed offline selection under its original %s slug", async (slug) => {
        const parsed = new JSDOM(templates["core/clients/offline_detail.html"]);
        const form = findInTemplates(parsed.window.document, '[data-testid="offline-edit-form"]');
        const loop = form.querySelector("template[x-for]").outerHTML;
        parsed.window.close();
        const { dom, win, errors } = createPage(`<div x-data="offlineClientView">${loop}</div>`, ["offline-client-view.js"]);
        try {
            Object.defineProperty(win.navigator, "onLine", { value: false });
            win.offlineEdit = { markEventModified: vi.fn(async () => ({ pk: "event-1" })) };
            await startAlpine(win);
            const component = win.Alpine.$data(win.document.querySelector("div"));
            const event = { pk: "event-1", document_type_pk: "doctype-1", data_fields: { [slug]: ["local-option"] } };
            component.data = { documentTypes: [{ pk: "doctype-1", fields: [{
                slug, name: "Selection", field_type: "multi_select",
                options: [{ value: "local-option", label: "Local" }, { value: "server-option", label: "Server" }],
            }] }], events: [event] };
            component.load = async () => {};
            component.startEdit(event);
            await win.Alpine.nextTick();
            const select = win.document.querySelector("select[multiple]");
            expect(select).not.toBeNull();
            expect(Array.from(select.selectedOptions, (option) => option.value)).toEqual(["local-option"]);
            select.value = "server-option";
            select.dispatchEvent(new win.Event("change", { bubbles: true }));
            await win.Alpine.nextTick();
            await component.saveEdit(event);
            expect(win.offlineEdit.markEventModified).toHaveBeenCalledOnce();
            expect(JSON.parse(JSON.stringify(win.offlineEdit.markEventModified.mock.calls[0][1]))).toEqual({ [slug]: ["server-option"] });
            expect(errors).toEqual([]);
        } finally {
            dom.window.close();
        }
    });

    it.each(["client", "shell"].flatMap((mode) => slugs.map((slug) => [mode, slug])))(
        "creates an event through the shared field template in %s mode for %s", async (mode, slug) => {
            const componentName = mode === "client" ? "offlineClientView" : "offlineEventCreate";
            const module = mode === "client" ? "offline-client-view.js" : "offline-create.js";
            const html = `<div x-data="${componentName}">${templates["core/clients/_offline_event_fields.html"]}</div>`;
            const { dom, win, errors } = createPage(html, [module]);
            try {
                Object.defineProperty(win.navigator, "onLine", { value: false });
                win.offlineEdit = { markEventNew: vi.fn(async () => ({ pk: "new-event" })) };
                await startAlpine(win);
                const component = win.Alpine.$data(win.document.querySelector("div"));
                const documentTypes = [{ pk: "doctype-1", name: "Selection", fields: [{
                    slug, name: "Selection", field_type: "multi_select",
                    options: [{ value: "server-option", label: "Server" }],
                }] }];
                if (mode === "client") {
                    component.data = { documentTypes, events: [] };
                    component.load = async () => {};
                    component.startCreate();
                } else component.facility = { documentTypes };
                component.createDocTypePk = "doctype-1";
                component.createOccurredAt = "2026-10-09T10:00";
                component.onCreateDocTypeChange();
                await win.Alpine.nextTick();
                const select = win.document.querySelector("select[multiple]");
                expect(select).not.toBeNull();
                select.value = "server-option";
                select.dispatchEvent(new win.Event("change", { bubbles: true }));
                await win.Alpine.nextTick();
                await component.saveCreate();
                expect(win.offlineEdit.markEventNew).toHaveBeenCalledOnce();
                expect(JSON.parse(JSON.stringify(win.offlineEdit.markEventNew.mock.calls[0][2]))).toEqual({ [slug]: ["server-option"] });
                expect(errors).toEqual([]);
            } finally {
                dom.window.close();
            }
        }
    );
});
