// An edit the server rejected during the background replay ("dead", reason
// "invalid") must stay correctable on the person's offline view: the edit
// button is offered when the person was allowed to edit the event before, the
// form is prefilled with the stored values and shows the rejected fields, and
// saving turns the entry back into a sendable change that goes out exactly
// once. Real store, real crypto, real bundle read and the real component; only
// the network and the revocation check are replaced.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Dexie from "dexie";

import { importOfflineStoreModules } from "./_offline_store_modules.mjs";

const PK_CLIENT = "99999999-9999-4999-8999-999999999999";
const PK_EVENT = "11111111-1111-4111-8111-111111111111";
const PK_OTHER = "22222222-2222-4222-8222-222222222222";
const PK_TASK = "44444444-4444-4444-8444-444444444444";
const TOKEN = "2026-11-01T10:00:00+00:00";

let factory;
let component;
let store;

function onLine(value) {
    Object.defineProperty(window.navigator, "onLine", { configurable: true, value: value });
}

function bundle({ canEdit = true } = {}) {
    return {
        client: { pk: PK_CLIENT, data_fields: {}, field_metadata: [] },
        document_types: [
            {
                pk: "dt1",
                name: "Counselling",
                fields: [{ slug: "dauer", name: "Dauer", field_type: "number" }],
            },
        ],
        events: [
            {
                pk: PK_EVENT,
                occurred_at: "2026-11-01T09:00:00+00:00",
                updated_at: TOKEN,
                document_type_pk: "dt1",
                document_type_name: "Counselling",
                data_fields: { dauer: "5" },
                can_edit: canEdit,
            },
            {
                pk: PK_OTHER,
                occurred_at: "2026-11-01T08:00:00+00:00",
                updated_at: TOKEN,
                document_type_pk: "dt1",
                document_type_name: "Counselling",
                data_fields: { dauer: "3" },
                can_edit: canEdit,
            },
        ],
        cases: [],
        workitems: [
            {
                pk: PK_TASK,
                title: "Call back",
                item_type: "task",
                priority: "normal",
                status: "open",
                due_date: "",
                remind_at: "",
                recurrence: "none",
                assigned_to_pk: "",
                updated_at: TOKEN,
                can_edit: canEdit,
            },
        ],
        assignable_users: [],
        generated_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 3600 * 1000).toISOString(),
        ttl: 3600,
        schema_version: 3,
    };
}

function rejection(errors) {
    return { redirected: false, status: 422, json: async () => ({ error: "invalid", errors: errors }) };
}

function saved() {
    return {
        redirected: false,
        status: 200,
        headers: new Headers({ "X-Offline-Replay-Result": "saved" }),
    };
}

async function loadModules() {
    vi.resetModules();
    delete window.offlineStore;
    delete window.offlineEdit;
    delete window.offlineClient;
    window.Dexie = Dexie;
    globalThis.Dexie = Dexie;
    onLine(true);
    window.csrfUtils = {
        fromMeta: vi.fn(() => "csrf-token"),
        refresh: vi.fn(async () => "fresh-csrf-token"),
    };
    window.URL_PATTERNS = { extractClientPk: () => PK_CLIENT };
    window.Alpine = {
        addInitSelector: vi.fn(),
        directive: vi.fn(),
        data: (name, fn) => {
            if (name === "offlineClientView") factory = fn;
        },
    };
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.deriveSessionKey("correct horse battery", "c2FsdHNhbHRzYWx0");
    await importOfflineStoreModules();
    await import("../../src/static/js/date-input-i18n.js");
    await import("../../src/static/js/offline-form-fields.js");
    await import("../../src/static/js/offline-edit.js");
    await import("../../src/static/js/offline-client-view.js");
    document.dispatchEvent(new Event("alpine:init"));
    store = window.offlineStore;
    window.URL_PATTERNS = { extractClientPk: () => PK_CLIENT };
    window.offlineClient = store;
    // Replay classification assumes a previously validated revocation list.
    store.isRevocationPending = async () => false;
    store.applyClientRevocations = vi.fn(async () => ({ status: "ok" }));
}

async function openPerson(options) {
    await store.saveClientBundle(bundle(options));
    const root = document.createElement("div");
    root.setAttribute("data-pk", PK_CLIENT);
    document.body.append(root);
    component = factory();
    component.$el = root;
    component.init();
    component._reconcile = vi.fn(async () => {});
    await component.load();
}

function eventByPk(pk) {
    return component.data.events.find((e) => e.pk === pk);
}

// What the background replay does to a stored edit the server rejects: the
// edit is made through the form code path (markEventModified), the replay
// answers 422 and marks it dead.
async function makeRejectedEdit() {
    onLine(false);
    await openPerson();
    component.startEdit(eventByPk(PK_EVENT));
    component.editValues.dauer = "11";
    await component.saveEdit(eventByPk(PK_EVENT));
    onLine(true);
    vi.stubGlobal(
        "fetch",
        vi.fn(async () => rejection({ dauer: [{ message: "Wert 11 (Klientin Muster).", code: "max_value" }] })),
    );
    await window.offlineEdit.replayAllModifiedEvents();
    vi.unstubAllGlobals();
    await component.load();
}

beforeEach(async () => {
    await loadModules();
    await store.db.events.clear();
    await store.db.clients.clear();
});

afterEach(() => {
    if (component && typeof component.destroy === "function") component.destroy();
    component = null;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    document.body.innerHTML = "";
});

describe("a background-rejected edit of an existing event", () => {
    it("can be edited again, prefilled with the stored values and showing the rejected fields", async () => {
        await makeRejectedEdit();

        const ev = eventByPk(PK_EVENT);
        expect(ev.localStatus).toBe("dead");
        expect(ev.is_dead).toBe(true);
        expect(ev.can_edit_ui).toBe(true);

        component.startEdit(ev);
        expect(component.editValues.dauer).toBe("11");
        expect(component.editError).toContain("dauer");
        expect(component.editError).toContain("max_value");
        expect(component.editError).not.toContain("Muster");
    });

    it("turns into one sendable change on save, which is sent exactly once", async () => {
        await makeRejectedEdit();
        const ev = eventByPk(PK_EVENT);
        expect(ev.can_edit_ui).toBe(true);
        component.startEdit(ev);
        component.editValues.dauer = "8";
        onLine(false);

        await component.saveEdit(ev);

        const row = await store.db.events.get(PK_EVENT);
        expect(row.localStatus).toBe("modified");
        expect(await store.listDeadEvents()).toEqual([]);
        expect(await store.hasReplayableWork()).toBe(true);

        onLine(true);
        const post = vi.fn(async () => saved());
        vi.stubGlobal("fetch", post);
        await window.offlineEdit.replayAllModifiedEvents();
        await window.offlineEdit.replayAllModifiedEvents();

        expect(post).toHaveBeenCalledTimes(1);
        expect(post.mock.calls[0][0]).toContain(PK_EVENT);
        expect(await store.hasReplayableWork()).toBe(false);
    });

    it("keeps the edit when the corrected save is rejected again while the form is open", async () => {
        await makeRejectedEdit();
        const ev = eventByPk(PK_EVENT);
        expect(ev.can_edit_ui).toBe(true);
        component.startEdit(ev);
        component.editValues.dauer = "12";
        const post = vi.fn(async () => rejection({ dauer: [{ message: "zu gross", code: "max_value" }] }));
        vi.stubGlobal("fetch", post);

        await component.saveEdit(ev);

        expect(post).toHaveBeenCalledTimes(1);
        expect((await store.db.events.get(PK_EVENT)).localStatus).toBe("modified");
        expect(component.lastSyncResult).toBe("invalid");
    });

    it("offers no edit on a sibling edit that was already sent, while the rejected one stays correctable", async () => {
        onLine(false);
        await openPerson();
        for (const [pk, value] of [
            [PK_EVENT, "11"],
            [PK_OTHER, "4"],
        ]) {
            component.startEdit(eventByPk(pk));
            component.editValues.dauer = value;
            await component.saveEdit(eventByPk(pk));
        }
        onLine(true);
        vi.stubGlobal(
            "fetch",
            vi.fn(async (url) =>
                String(url).includes(PK_EVENT)
                    ? rejection({ dauer: [{ message: "zu gross", code: "max_value" }] })
                    : saved(),
            ),
        );
        await window.offlineEdit.replayAllModifiedEvents();
        vi.unstubAllGlobals();
        await component.load();

        // The rejected edit keeps the correction route ...
        expect(eventByPk(PK_EVENT).localStatus).toBe("dead");
        expect(eventByPk(PK_EVENT).can_edit_ui).toBe(true);
        // ... the sent one is stored as a finished envelope; editing it again would
        // replay the token from before its own save and raise a conflict.
        expect(eventByPk(PK_OTHER).localStatus).toBe("clean");
        expect(eventByPk(PK_OTHER).can_edit_ui).toBe(false);
    });

    it("stays read-only when the person was not allowed to edit the event", async () => {
        // Server says can_edit=false: no edit button before the rejection ...
        onLine(false);
        await openPerson({ canEdit: false });
        expect(eventByPk(PK_EVENT).can_edit_ui).toBe(false);
        // ... and a rejected stored edit (written by an earlier build / another
        // route) does not become editable either.
        const canEditFalse = await store.db.events.get(PK_EVENT);
        expect(canEditFalse.localStatus).toBe("clean");
        await store.saveOfflineEdit({
            pk: PK_EVENT,
            clientPk: PK_CLIENT,
            localStatus: "modified",
            data: {
                formData: { dauer: "11" },
                expectedUpdatedAt: TOKEN,
                documentTypeName: "Counselling",
                documentTypePk: "dt1",
                occurredAt: "2026-11-01T09:00:00+00:00",
                canEdit: false,
            },
        });
        await store.markEventDead(PK_EVENT, "invalid", JSON.stringify({ dauer: ["max_value"] }));
        await component.load();

        const ev = eventByPk(PK_EVENT);
        expect(ev.is_dead).toBe(true);
        expect(ev.can_edit_ui).toBe(false);
    });

    it("is not editable for other dead reasons or when the event was never created on the server", async () => {
        onLine(false);
        await openPerson();
        const envelope = (extra) => ({
            formData: { dauer: "11" },
            expectedUpdatedAt: TOKEN,
            documentTypeName: "Counselling",
            documentTypePk: "dt1",
            occurredAt: "2026-11-01T09:00:00+00:00",
            canEdit: true,
            ...extra,
        });
        await store.saveOfflineEdit({
            pk: PK_EVENT,
            clientPk: PK_CLIENT,
            localStatus: "modified",
            data: envelope(),
        });
        await store.markEventDead(PK_EVENT, "not-found", "404");
        await store.saveOfflineEdit({
            pk: PK_OTHER,
            clientPk: PK_CLIENT,
            localStatus: "new",
            data: envelope({ idempotencyKey: "k" }),
        });
        await store.markEventDead(PK_OTHER, "invalid", JSON.stringify({ dauer: ["max_value"] }));
        await component.load();

        expect(eventByPk(PK_EVENT).can_edit_ui).toBe(false);
        expect(eventByPk(PK_OTHER).is_dead).toBe(true);
        expect(eventByPk(PK_OTHER).can_edit_ui).toBe(false);
    });
});

describe("a background-rejected edit of a task", () => {
    function taskByPk(pk) {
        return component.data.workitems.find((w) => w.pk === pk);
    }

    async function makeRejectedTaskEdit() {
        onLine(false);
        await openPerson();
        await window.offlineEdit.markWorkItemModified(
            PK_TASK,
            { item_type: "task", title: "Call back tomorrow", priority: "normal", recurrence: "none" },
            { clientPk: PK_CLIENT, expectedUpdatedAt: TOKEN },
        );
        onLine(true);
        vi.stubGlobal(
            "fetch",
            vi.fn(async () => rejection({ title: [{ message: "Titel (Klientin Muster).", code: "max_length" }] })),
        );
        await window.offlineEdit.replayAllModifiedEvents();
        vi.unstubAllGlobals();
        await component.load();
    }

    it("can be edited again, prefilled and showing the rejected fields", async () => {
        await makeRejectedTaskEdit();

        const wi = taskByPk(PK_TASK);
        expect(wi.localStatus).toBe("dead");
        expect(wi.can_edit_ui).toBe(true);
        component.startEditWorkItem(wi);
        expect(component.wiValues.title).toBe("Call back tomorrow");
        expect(component.wiError).toContain("title");
        expect(component.wiError).toContain("max_length");
        expect(component.wiError).not.toContain("Muster");
    });

    it("turns into one sendable change on save, which is sent exactly once", async () => {
        await makeRejectedTaskEdit();
        const wi = taskByPk(PK_TASK);
        component.startEditWorkItem(wi);
        component.wiValues.title = "Call back";
        component.validateWorkItem = () => true;
        onLine(false);

        await component.saveEditWorkItem(wi);

        expect((await store.db.events.get(PK_TASK)).localStatus).toBe("modified");
        expect(await store.listDeadEvents()).toEqual([]);

        onLine(true);
        const post = vi.fn(async () => saved());
        vi.stubGlobal("fetch", post);
        await window.offlineEdit.replayAllModifiedEvents();
        await window.offlineEdit.replayAllModifiedEvents();

        expect(post).toHaveBeenCalledTimes(1);
        expect(post.mock.calls[0][0]).toContain("/workitems/" + PK_TASK + "/edit/");
    });

    it("stays read-only when the server did not allow the person to edit the task", async () => {
        onLine(false);
        await store.saveClientBundle(bundle({ canEdit: false }));
        await store.saveOfflineEdit({
            pk: PK_TASK,
            clientPk: PK_CLIENT,
            localStatus: "modified",
            data: { kind: "workitem", formData: { title: "x" }, expectedUpdatedAt: TOKEN },
        });
        await store.markEventDead(PK_TASK, "invalid", JSON.stringify({ title: ["max_length"] }));
        const root = document.createElement("div");
        root.setAttribute("data-pk", PK_CLIENT);
        document.body.append(root);
        component = factory();
        component.$el = root;
        component.init();
        await component.load();

        expect(taskByPk(PK_TASK).localStatus).toBe("dead");
        expect(taskByPk(PK_TASK).can_edit_ui).toBe(false);
    });
});
