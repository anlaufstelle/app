import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const factories = {};
beforeAll(async () => {
    window.Alpine = { data: (name, factory) => { factories[name] = factory; } };
    await import("../../src/static/js/offline-form-fields.js");
    await import("../../src/static/js/offline-create.js");
    await import("../../src/static/js/offline-client-view.js");
    document.dispatchEvent(new Event("alpine:init"));
});

beforeEach(() => {
    document.body.innerHTML = "";
    Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
    window.offlineEdit = {
        markWorkItemNew: vi.fn(async () => ({ pk: "new-task" })),
        markWorkItemModified: vi.fn(async () => ({ pk: "edited-task" })),
    };
});

function component(path) {
    const c = factories[path === "shell" ? "offlineWorkItemCreate" : "offlineClientView"]();
    const root = document.createElement("div");
    root.setAttribute("x-data", path === "shell" ? "offlineWorkItemCreate" : "offlineClientView");
    root.innerHTML = ["title", "due_date", "remind_at"].map(field =>
        `<input id="test-${field}" data-wi-field="${field}" data-wi-error-id="error-${field}">
        <p id="error-${field}" role="alert" hidden></p>`).join("");
    document.body.append(root);
    c.$el = root;
    c.wiValues = window.offlineFormFields.emptyWorkItemValues();
    c.wiValues.title = "Valid title";
    c.data = { workitems: [] };
    c.load = vi.fn(async () => {});
    c._pk = "client-1";
    return c;
}

async function save(c, path) {
    if (path === "shell") return c.saveCreate();
    if (path === "create") return c.saveCreateWorkItem();
    return c.saveEditWorkItem({ pk: "edited-task", updated_at: "clock" });
}

describe.each(["shell", "create", "edit"])("%s offline task validation", path => {
    it.each([
        ["", "", "", "title"], [" \t\n ", "", "", "title"],
        ["a".repeat(201), "", "", "title"], ["😀".repeat(201), "", "", "title"],
        ["e\u0301".repeat(100) + "x", "", "", "title"],
        ["Valid", "2026-10-14", "2026-10-15", "remind_at"],
    ])("rejects invalid values before queue writes", async (title, due, remind, field) => {
        const c = component(path);
        Object.assign(c.wiValues, { title, due_date: due, remind_at: remind });
        const original = { ...c.wiValues };
        await save(c, path);
        expect(window.offlineEdit.markWorkItemNew).not.toHaveBeenCalled();
        expect(window.offlineEdit.markWorkItemModified).not.toHaveBeenCalled();
        expect(c.saved).not.toBe(true);
        expect(c.lastSyncResult).not.toBe("pending");
        expect(c.wiValues).toEqual(original);
        const input = c.$el.querySelector(`[data-wi-field="${field}"]`);
        expect(input.getAttribute("aria-invalid")).toBe("true");
        expect(input.getAttribute("aria-describedby")).toBe(`error-${field}`);
        expect(document.getElementById(`error-${field}`).textContent).not.toBe("");
        expect(document.activeElement).toBe(input);
        Object.assign(c.wiValues, { title: "Corrected", due_date: "2026-10-14", remind_at: "2026-10-14" });
        c.$el = input;
        c.clearWorkItemErrors();
        expect(input.hasAttribute("aria-invalid")).toBe(false);
        expect(input.hasAttribute("aria-describedby")).toBe(false);
        expect(document.getElementById(`error-${field}`).hidden).toBe(true);
    });

    it.each(["a".repeat(200), "😀".repeat(200), "e\u0301".repeat(100)])("accepts 200 codepoints and equal dates", async title => {
        const c = component(path);
        Object.assign(c.wiValues, { title: `  ${title}  `, due_date: "2020-01-01", remind_at: "2020-01-01" });
        await save(c, path);
        const writer = path === "edit" ? window.offlineEdit.markWorkItemModified : window.offlineEdit.markWorkItemNew;
        expect(writer).toHaveBeenCalledOnce();
        expect(writer.mock.calls[0][1].title).toBe(title);
        if (path === "shell") expect(c.saved).toBe(true);
        else expect(c.lastSyncResult).toBe("pending");
    });

    it.each(["\u0085", "\u001c", "\u001d", "\u001e", "\u001f", "\u00a0", "\u2028"])("matches Python edge whitespace U+%s", async whitespace => {
        const c = component(path);
        c.wiValues.title = whitespace;
        await save(c, path);
        expect(window.offlineEdit.markWorkItemNew).not.toHaveBeenCalled();
        expect(window.offlineEdit.markWorkItemModified).not.toHaveBeenCalled();
        expect(c.saved).not.toBe(true);
        expect(c.lastSyncResult).not.toBe("pending");
        expect(c.wiValues.title).toBe(whitespace);
        expect(c.$el.querySelector('[data-wi-field="title"]').getAttribute("aria-invalid")).toBe("true");
        for (const title of [whitespace + "a".repeat(201) + whitespace, whitespace + "😀".repeat(201) + whitespace]) {
            c.wiValues.title = title;
            await save(c, path);
            expect(window.offlineEdit.markWorkItemNew).not.toHaveBeenCalled();
            expect(window.offlineEdit.markWorkItemModified).not.toHaveBeenCalled();
        }
        for (const title of ["a".repeat(200), "😀".repeat(200), "Normal text"]) {
            c.wiValues.title = whitespace + title + whitespace;
            await save(c, path);
            const writer = path === "edit" ? window.offlineEdit.markWorkItemModified : window.offlineEdit.markWorkItemNew;
            expect(writer.mock.lastCall[1].title).toBe(title);
        }
    });

    it("preserves interior whitespace and Python non-whitespace at the edges", async () => {
        const c = component(path);
        const title = "\ufeffNormal\u0085text\ufeff";
        c.wiValues.title = title;
        await save(c, path);
        const writer = path === "edit" ? window.offlineEdit.markWorkItemModified : window.offlineEdit.markWorkItemNew;
        expect(writer).toHaveBeenCalledOnce();
        expect(writer.mock.calls[0][1].title).toBe(title);
    });

    it("focuses title before a simultaneous reminder error and uses root translations", async () => {
        const c = component(path);
        c.$el.dataset.wiTitleRequired = "Localized required title";
        Object.assign(c.wiValues, { title: " ", due_date: "2026-10-14", remind_at: "2026-10-15" });
        await save(c, path);
        expect(document.activeElement.id).toBe("test-title");
        expect(document.getElementById("error-title").textContent).toBe("Localized required title");
    });
});

it("clears accessible errors through the real CSP input handler after x-model updates", async () => {
    const { default: Alpine } = await import("@alpinejs/csp");
    const c = component("shell");
    const root = c.$el;
    delete c.$el;
    c.init = () => {};
    Alpine.data("validationFixture", () => c);
    root.setAttribute("x-data", "validationFixture");
    root.innerHTML = `<form @submit.prevent="saveCreate">${root.innerHTML}</form>`;
    const title = root.querySelector('[data-wi-field="title"]');
    title.setAttribute("x-model", "wiValues.title");
    title.setAttribute("x-on:input", "clearWorkItemErrors");
    Alpine.initTree(root);
    title.value = " ";
    title.dispatchEvent(new Event("input", { bubbles: true }));
    root.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await Alpine.nextTick();
    expect(title.getAttribute("aria-invalid")).toBe("true");
    expect(window.offlineEdit.markWorkItemNew).not.toHaveBeenCalled();
    title.value = "Corrected";
    title.dispatchEvent(new Event("input", { bubbles: true }));
    await Alpine.nextTick();
    expect(title.hasAttribute("aria-invalid")).toBe(false);
    expect(document.getElementById("error-title").hidden).toBe(true);
    Alpine.destroyTree(root);
});
