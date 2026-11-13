import { afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import Alpine from "@alpinejs/csp";
import { readFileSync } from "node:fs";

const template = readFileSync("src/templates/core/clients/offline_detail.html", "utf8");
const loadingCondition = template.match(/<template x-if="([^"]+)">\s*<div[^>]*data-testid="offline-loading"/)[1];

// Exercise real Alpine DOM updates, including the available-view x-if used by
// offline_detail.html. State-only tests cannot see a destroyed form node.
let root;
let state;
let finishRead;

function bundle() {
    return {
        client: { pk: "client-1", data_fields: {} },
        events: [{
            pk: "event-1", document_type_pk: "note", data_fields: { note: "Original" },
            can_edit: true, updated_at: "version-1", localStatus: "clean",
        }],
        workitems: [], cases: [],
        documentTypes: [{ pk: "note", fields: [{ slug: "note", field_type: "text" }] }],
        assignableUsers: [], lastSynced: Date.now(),
    };
}

beforeAll(async () => {
    window.Alpine = Alpine;
    await import("../../src/static/js/offline-form-fields.js");
    await import("../../src/static/js/offline-client-view.js");
    document.dispatchEvent(new Event("alpine:init"));
});

beforeEach(async () => {
    Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
    window.offlineClient = { getOfflineClient: vi.fn(async () => bundle()) };
    window.offlineEdit = {
        markWorkItemNew: vi.fn(async () => ({ pk: "task-1" })),
        markEventModified: vi.fn(async () => ({ pk: "event-1" })),
    };
    root = document.createElement("div");
    root.setAttribute("x-data", "offlineClientView");
    root.dataset.pk = "client-1";
    root.innerHTML = `
        <template x-if="${loadingCondition}"><p id="loading">Loading</p></template>
        <button id="new-task" @click="startCreateWorkItem">New task</button>
        <template x-if="showAvailable"><div>
            <template x-if="creatingWorkItem">
                <form @submit.prevent="saveCreateWorkItem">
                    <input id="title" x-model="wiValues.title" @input="clearWorkItemErrors"
                        data-wi-field="title" data-wi-error-id="title-error" required>
                    <p id="title-error" role="alert" hidden></p>
                    <button id="save" type="submit" :disabled="saving">Save</button>
                </form>
            </template>
            <template x-for="event in data.events" :key="event.pk"><article>
                <span id="displayed-note" x-text="event.data_fields.note"></span>
                <button id="edit" @click="startEdit(event)">Edit</button>
                <template x-if="event.editing"><form @submit.prevent="saveEdit(event)">
                    <input id="note" x-model="editValues.note">
                    <button id="save-edit" type="submit" :disabled="saving">Save</button>
                </form></template>
            </article></template>
        </div></template>`;
    document.body.append(root);
    Alpine.initTree(root);
    state = Alpine.$data(root);
    await state.load();
    await Alpine.nextTick();
    root.querySelector("#new-task").click();
    await Alpine.nextTick();
});

afterEach(() => {
    Alpine.destroyTree(root);
    root.remove();
    delete window.offlineClient;
    delete window.offlineEdit;
});

function enterTitle(value) {
    const title = root.querySelector("#title");
    title.value = value;
    title.dispatchEvent(new Event("input", { bubbles: true }));
}

async function beginCounterRefresh() {
    // Park exactly the next cache read, not a timed approximation of the race.
    let readStarted;
    const started = new Promise(resolve => { readStarted = resolve; });
    const pending = new Promise(resolve => { finishRead = () => resolve(bundle()); });
    window.offlineClient.getOfflineClient.mockImplementationOnce(() => {
        readStarted();
        return pending;
    });
    window.dispatchEvent(new CustomEvent("offline-unsynced-count", { detail: { count: 0 } }));
    await started;
    await Alpine.nextTick();
}

it("keeps an invalid task error visible after an unchanged counter refresh", async () => {
    enterTitle("\u0085 ");
    root.querySelector("#save").click();
    await Alpine.nextTick();
    expect(root.querySelector("#title-error").hidden).toBe(false);
    expect(state.saving).toBe(false);
    await beginCounterRefresh();
    finishRead();
    await Alpine.nextTick();
    expect(root.querySelector("#title").value).toBe("\u0085 ");
    expect(window.offlineEdit.markWorkItemNew).not.toHaveBeenCalled();
    expect(root.querySelector("#title-error").hidden).toBe(false);
    expect(root.querySelector("#title").getAttribute("aria-invalid")).toBe("true");
});

it("keeps the pressed edit-save button attached during an unchanged counter refresh", async () => {
    root.querySelector("#edit").click();
    await Alpine.nextTick();
    const note = root.querySelector("#note");
    note.value = "Edited note";
    note.dispatchEvent(new Event("input", { bubbles: true }));
    const save = root.querySelector("#save-edit");
    save.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    await beginCounterRefresh();
    expect.soft(save.isConnected).toBe(true);
    finishRead();
    await Alpine.nextTick();
    save.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    save.click();
    await Alpine.nextTick();
    expect(window.offlineEdit.markEventModified).toHaveBeenCalledOnce();
    expect(window.offlineEdit.markEventModified.mock.calls[0][1].note).toBe("Edited note");
    expect(state.lastSyncResult).toBe("pending");
});

it("removes the form and its values when a refresh loses the cached client", async () => {
    enterTitle("Private draft");
    window.offlineClient.getOfflineClient.mockResolvedValueOnce(null);
    await state.load();
    await Alpine.nextTick();
    expect(root.querySelector("form")).toBeNull();
    expect(state.data).toBeNull();
    expect(state.wiValues).toEqual({});
    expect(state.showUnavailable).toBe(true);
});

it("does not move an open form by inserting a loading indicator during refresh", async () => {
    await beginCounterRefresh();
    expect.soft(root.querySelector("#loading")).toBeNull();
    finishRead();
    await Alpine.nextTick();
});

function parkRead() {
    let resolve, reject, started;
    const ready = new Promise(done => { started = done; });
    const result = new Promise((yes, no) => { resolve = yes; reject = no; });
    window.offlineClient.getOfflineClient.mockImplementationOnce(() => {
        started();
        return result;
    });
    return { ready, resolve, reject };
}

it("keeps the saved value when a read started before saving finishes last", async () => {
    root.querySelector("#edit").click();
    await Alpine.nextTick();
    const note = root.querySelector("#note");
    note.value = "Edited note";
    note.dispatchEvent(new Event("input", { bubbles: true }));
    const save = root.querySelector("#save-edit");
    const read = parkRead();
    const oldLoad = state.load();
    await read.ready;
    await Alpine.nextTick();
    const saved = bundle();
    saved.events[0].data_fields.note = "Edited note";
    window.offlineEdit.markEventModified.mockImplementationOnce(async () => {
        window.offlineClient.getOfflineClient.mockResolvedValue(saved);
        return { pk: "event-1" };
    });
    expect(save.isConnected).toBe(true);
    save.click();
    await Alpine.nextTick();
    expect(window.offlineEdit.markEventModified).toHaveBeenCalledOnce();
    expect(root.querySelector("#displayed-note").textContent).toBe("Edited note");
    read.resolve(bundle());
    await oldLoad;
    await Alpine.nextTick();
    expect(root.querySelector("#displayed-note").textContent).toBe("Edited note");
    expect(state.available).toBe(true);
    expect(state.loading).toBe(false);
});

it.each(["missing", "expired", "error"])("ignores an obsolete %s read after a newer successful read", async (outcome) => {
    const read = parkRead();
    const oldLoad = state.load();
    await read.ready;
    await state.load();
    const current = state.data;
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
        if (outcome === "error") read.reject(new Error("Read failed"));
        else read.resolve(outcome === "missing" ? null : { ...bundle(), expiresAt: "2000-01-01" });
        await oldLoad;
        expect(state.data).toBe(current);
        expect(state.available).toBe(true);
        expect(state.loading).toBe(false);
    } finally {
        log.mockRestore();
    }
});

it("keeps loading true while the newest read is pending", async () => {
    const first = parkRead();
    const oldLoad = state.load();
    await first.ready;
    const second = parkRead();
    const newLoad = state.load();
    await second.ready;
    first.resolve(bundle());
    await oldLoad;
    expect.soft(state.loading).toBe(true);
    second.resolve(bundle());
    await newLoad;
    expect(state.loading).toBe(false);
});
