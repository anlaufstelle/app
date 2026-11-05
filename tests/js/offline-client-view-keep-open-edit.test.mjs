// Refs #2688: offline-edit.js fires the sync counter events on
// DOMContentLoaded (even when nothing changed). The offline client view
// answers every counter event with _reconcile() -> load(), which rebuilds
// ``data`` from IndexedDB with every ``editing`` flag reset to false. When
// that rebuild lands after the user clicked "edit", the inline form they had
// just opened vanished together with their input.
//
// The open inline form must survive a rebuild until the person saves or
// cancels; the list behind it still refreshes. This spec covers the
// component state machine (Alpine template rendering is covered by the
// browser runs in test_offline_edit_conflict.py / test_offline_workitem_ui.py).
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

let factory;
let liveComponents = [];

const PK_CLIENT = "11111111-1111-4111-8111-111111111111";
const PK_EVENT_A = "22222222-2222-4222-8222-222222222222";
const PK_EVENT_B = "33333333-3333-4333-8333-333333333333";
const PK_WI_A = "44444444-4444-4444-8444-444444444444";
const PK_WI_B = "55555555-5555-4555-8555-555555555555";

beforeAll(async () => {
    window.URL_PATTERNS = { extractClientPk: () => PK_CLIENT };
    window.Alpine = {
        addInitSelector: vi.fn(),
        directive: vi.fn(),
        data: (name, fn) => {
            if (name === "offlineClientView") factory = fn;
        },
    };
    await import("../../src/static/js/date-input-i18n.js");
    await import("../../src/static/js/offline-form-fields.js");
    await import("../../src/static/js/offline-client-view.js");
    document.dispatchEvent(new Event("alpine:init"));
});

afterEach(() => {
    liveComponents.forEach((c) => c.destroy());
    liveComponents = [];
    delete window.offlineClient;
    delete window.offlineEdit;
    delete window.crypto_session;
    document.body.innerHTML = "";
});

// A fresh bundle as getOfflineClient returns it; every call builds new
// objects, just like the real IndexedDB read.
function bundle({
    noteA = "a",
    noteB = "b",
    titleA = "Task A",
    titleB = "Task B",
    statusA = "clean",
    withA = true,
    tokenA = "2026-11-01T09:00:00.000000+00:00",
    tokenB = "2026-11-01T10:00:00.000000+00:00",
} = {}) {
    const events = [
        {
            pk: PK_EVENT_B,
            document_type_pk: "dt1",
            occurred_at: "2026-11-01T10:00:00Z",
            data_fields: { note: noteB },
            updated_at: tokenB,
            can_edit: true,
            localStatus: "clean",
        },
    ];
    const workitems = [
        {
            pk: PK_WI_B,
            title: titleB,
            item_type: "task",
            priority: "normal",
            updated_at: tokenB,
            can_edit: true,
            localStatus: "clean",
        },
    ];
    if (withA) {
        events.unshift({
            pk: PK_EVENT_A,
            document_type_pk: "dt1",
            occurred_at: "2026-11-01T09:00:00Z",
            data_fields: { note: noteA },
            updated_at: tokenA,
            can_edit: true,
            localStatus: statusA,
        });
        workitems.unshift({
            pk: PK_WI_A,
            title: titleA,
            item_type: "task",
            priority: "normal",
            updated_at: tokenA,
            can_edit: true,
            localStatus: statusA,
        });
    }
    return {
        pk: PK_CLIENT,
        client: { pk: PK_CLIENT, data_fields: {}, field_metadata: [] },
        documentTypes: [{ pk: "dt1", name: "Note", fields: [{ slug: "note", name: "Note", field_type: "text" }] }],
        events: events,
        cases: [],
        workitems: workitems,
        assignableUsers: [],
        lastSynced: Date.now(),
    };
}

async function makeLoadedComponent() {
    const root = document.createElement("div");
    root.setAttribute("data-pk", PK_CLIENT);
    document.body.append(root);
    const component = factory();
    component.$el = root;
    component.init();
    liveComponents.push(component);
    window.offlineClient = { getOfflineClient: vi.fn(async () => bundle()) };
    await component.load();
    return component;
}

function eventByPk(component, pk) {
    return component.data.events.find((e) => e.pk === pk);
}

function workItemByPk(component, pk) {
    return component.data.workitems.find((w) => w.pk === pk);
}

function deferred() {
    let resolve;
    const promise = new Promise((r) => {
        resolve = r;
    });
    return { promise, resolve };
}

async function flush() {
    for (let i = 0; i < 5; i += 1) {
        await new Promise((r) => setTimeout(r, 0));
    }
}

describe("offline client view keeps an open inline edit across a rebuild", () => {
    it("keeps the event form open with its input when a counter event rebuilds the list", async () => {
        const component = await makeLoadedComponent();
        component.startEdit(eventByPk(component, PK_EVENT_A));
        component.editValues.note = "typed by the user";
        const fields = component.editFields;
        const values = component.editValues;

        window.dispatchEvent(new CustomEvent("offline-unsynced-count", { detail: { count: 0 } }));
        await flush();

        expect(window.offlineClient.getOfflineClient).toHaveBeenCalledTimes(2);
        expect(eventByPk(component, PK_EVENT_A).editing).toBe(true);
        expect(eventByPk(component, PK_EVENT_B).editing).toBe(false);
        expect(component.editFields).toBe(fields);
        expect(component.editValues).toBe(values);
        expect(component.editValues.note).toBe("typed by the user");
    });

    it("keeps the task form open with its input when _reconcile() rebuilds the list", async () => {
        const component = await makeLoadedComponent();
        component.startEditWorkItem(workItemByPk(component, PK_WI_A));
        component.wiValues.title = "typed by the user";
        const values = component.wiValues;

        await component._reconcile();

        expect(workItemByPk(component, PK_WI_A).editing).toBe(true);
        expect(workItemByPk(component, PK_WI_B).editing).toBe(false);
        expect(component.wiValues).toBe(values);
        expect(component.wiValues.title).toBe("typed by the user");
    });

    it("still refreshes the other entries behind an open form", async () => {
        const component = await makeLoadedComponent();
        component.startEdit(eventByPk(component, PK_EVENT_A));
        window.offlineClient.getOfflineClient.mockImplementation(async () => bundle({ noteB: "b2", titleB: "Task B2" }));

        await component.load();

        expect(eventByPk(component, PK_EVENT_A).editing).toBe(true);
        expect(eventByPk(component, PK_EVENT_B).data_fields.note).toBe("b2");
        expect(eventByPk(component, PK_EVENT_B).data_fields_pairs[0].value_fmt).toBe("b2");
        expect(workItemByPk(component, PK_WI_B).title).toBe("Task B2");
    });

    it("does not revive an event form cancelled while a rebuild was in flight", async () => {
        const component = await makeLoadedComponent();
        component.startEdit(eventByPk(component, PK_EVENT_A));
        const pending = deferred();
        window.offlineClient.getOfflineClient.mockImplementationOnce(() => pending.promise);

        const loading = component.load();
        component.cancelEdit();
        pending.resolve(bundle());
        await loading;

        expect(component.data.events.every((e) => e.editing === false)).toBe(true);
    });

    it("does not revive a task form cancelled while a rebuild was in flight", async () => {
        const component = await makeLoadedComponent();
        component.startEditWorkItem(workItemByPk(component, PK_WI_A));
        const pending = deferred();
        window.offlineClient.getOfflineClient.mockImplementationOnce(() => pending.promise);

        const loading = component.load();
        component.cancelEditWorkItem();
        pending.resolve(bundle());
        await loading;

        expect(component.data.workitems.every((w) => w.editing === false)).toBe(true);
    });

    it("does not revive an event form saved while a rebuild was in flight", async () => {
        const component = await makeLoadedComponent();
        component.startEdit(eventByPk(component, PK_EVENT_A));
        const pending = deferred();
        window.offlineClient.getOfflineClient.mockImplementationOnce(() => pending.promise);
        window.offlineEdit = { markEventModified: vi.fn(async (pk) => ({ pk: pk })) };
        const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);

        try {
            const loading = component.load();
            await component.saveEdit(eventByPk(component, PK_EVENT_A));
            pending.resolve(bundle({ statusA: "modified" }));
            await loading;
        } finally {
            online.mockRestore();
        }

        expect(window.offlineEdit.markEventModified).toHaveBeenCalledTimes(1);
        expect(component.lastSyncResult).toBe("pending");
        expect(component.data.events.every((e) => e.editing === false)).toBe(true);
    });

    it("does not revive a task form saved while a rebuild was in flight", async () => {
        const component = await makeLoadedComponent();
        component.startEditWorkItem(workItemByPk(component, PK_WI_A));
        const pending = deferred();
        window.offlineClient.getOfflineClient.mockImplementationOnce(() => pending.promise);
        window.offlineEdit = { markWorkItemModified: vi.fn(async (pk) => ({ pk: pk })) };
        const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);

        try {
            const loading = component.load();
            await component.saveEditWorkItem(workItemByPk(component, PK_WI_A));
            pending.resolve(bundle({ statusA: "modified" }));
            await loading;
        } finally {
            online.mockRestore();
        }

        expect(window.offlineEdit.markWorkItemModified).toHaveBeenCalledTimes(1);
        expect(component.data.workitems.every((w) => w.editing === false)).toBe(true);
    });

    it("opens nothing when the edited entries are gone from the rebuilt list", async () => {
        const component = await makeLoadedComponent();
        component.startEdit(eventByPk(component, PK_EVENT_A));
        component.startEditWorkItem(workItemByPk(component, PK_WI_A));
        // startEditWorkItem closes the event form; reopen it so both kinds
        // are open when the rebuild drops their entries.
        eventByPk(component, PK_EVENT_A).editing = true;
        window.offlineClient.getOfflineClient.mockImplementation(async () => bundle({ withA: false, noteB: "b2" }));

        await expect(component.load()).resolves.toBeUndefined();

        expect(component.available).toBe(true);
        expect(component.data.events.map((e) => e.pk)).toEqual([PK_EVENT_B]);
        expect(component.data.events.every((e) => e.editing === false)).toBe(true);
        expect(component.data.workitems.every((w) => w.editing === false)).toBe(true);
        expect(eventByPk(component, PK_EVENT_B).data_fields.note).toBe("b2");
    });

    it("closes the form when the edited entry turned into a conflict meanwhile", async () => {
        const component = await makeLoadedComponent();
        component.startEdit(eventByPk(component, PK_EVENT_A));
        window.offlineClient.getOfflineClient.mockImplementation(async () => bundle({ statusA: "conflict" }));

        await component.load();

        expect(eventByPk(component, PK_EVENT_A).is_conflict).toBe(true);
        expect(eventByPk(component, PK_EVENT_A).editing).toBe(false);
    });

    it("closes the task form when the edited task turned into a conflict meanwhile", async () => {
        const component = await makeLoadedComponent();
        component.startEditWorkItem(workItemByPk(component, PK_WI_A));
        window.offlineClient.getOfflineClient.mockImplementation(async () => bundle({ statusA: "conflict" }));

        await component.load();

        expect(workItemByPk(component, PK_WI_A).is_conflict).toBe(true);
        expect(workItemByPk(component, PK_WI_A).editing).toBe(false);
    });

    it("closes the event form when the server changed the entry meanwhile", async () => {
        const component = await makeLoadedComponent();
        component.startEdit(eventByPk(component, PK_EVENT_A));
        component.editValues.note = "typed by the user";
        window.offlineClient.getOfflineClient.mockImplementation(async () =>
            bundle({ noteA: "changed elsewhere", tokenA: "2026-11-02T08:00:00.000000+00:00" })
        );

        await component._reconcile();

        // Saving the old field values with the refreshed token would slip past
        // the server's conflict check and overwrite the other change.
        expect(eventByPk(component, PK_EVENT_A).data_fields.note).toBe("changed elsewhere");
        expect(eventByPk(component, PK_EVENT_A).editing).toBe(false);
    });

    it("closes the task form when the server changed the task meanwhile", async () => {
        const component = await makeLoadedComponent();
        component.startEditWorkItem(workItemByPk(component, PK_WI_A));
        component.wiValues.title = "typed by the user";
        window.offlineClient.getOfflineClient.mockImplementation(async () =>
            bundle({ titleA: "changed elsewhere", tokenA: "2026-11-02T08:00:00.000000+00:00" })
        );

        await component._reconcile();

        expect(workItemByPk(component, PK_WI_A).title).toBe("changed elsewhere");
        expect(workItemByPk(component, PK_WI_A).editing).toBe(false);
    });

    it("keeps both forms open when only other entries got a new token", async () => {
        const component = await makeLoadedComponent();
        component.startEditWorkItem(workItemByPk(component, PK_WI_A));
        eventByPk(component, PK_EVENT_A).editing = true;
        const wiValues = component.wiValues;
        window.offlineClient.getOfflineClient.mockImplementation(async () =>
            bundle({ noteB: "b2", titleB: "Task B2", tokenB: "2026-11-02T08:00:00.000000+00:00" })
        );

        await component._reconcile();

        expect(eventByPk(component, PK_EVENT_A).editing).toBe(true);
        expect(workItemByPk(component, PK_WI_A).editing).toBe(true);
        expect(component.wiValues).toBe(wiValues);
        expect(eventByPk(component, PK_EVENT_B).data_fields.note).toBe("b2");
    });
});

// Refs #2688: while the view is unavailable (key cleared by logout, quick exit
// or idle wipe in another tab, a failed read, an expired bundle) the open form
// and its unsaved input must not stay in memory. Otherwise a later rebuild
// with the same token revives the previous session's draft, possibly for
// another person signed in on the same device, and saving would submit it
// under that account. Soft assertions report every leftover piece of state.
const UNAVAILABLE_PHASES = [
    ["the key was cleared (store returns null)", async () => null],
    [
        "the read failed",
        async () => {
            throw new Error("no key");
        },
    ],
    ["the bundle expired", async () => Object.assign(bundle(), { expiresAt: "2020-01-01T00:00:00Z" })],
];

describe("offline client view drops the edit state when the view becomes unavailable", () => {
    let consoleError;

    afterEach(() => {
        if (consoleError) consoleError.mockRestore();
        consoleError = undefined;
    });

    it.each(UNAVAILABLE_PHASES)("keeps the event form closed and empty after %s", async (_label, unavailableRead) => {
        consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
        const component = await makeLoadedComponent();
        component.startEdit(eventByPk(component, PK_EVENT_A));
        component.editValues.note = "draft of the previous session";

        window.offlineClient.getOfflineClient.mockImplementationOnce(unavailableRead);
        await component.load();
        expect.soft(component.available).toBe(false);
        expect.soft(component.data).toBe(null);
        expect.soft(component.editFields).toEqual([]);
        expect.soft(component.editValues).toEqual({});

        await component.load();

        expect.soft(component.available).toBe(true);
        expect.soft(component.data.events.every((e) => e.editing === false)).toBe(true);
        expect.soft(component.editFields).toEqual([]);
        expect.soft(component.editValues).toEqual({});
    });

    it.each(UNAVAILABLE_PHASES)("keeps the task form closed and empty after %s", async (_label, unavailableRead) => {
        consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
        const component = await makeLoadedComponent();
        component.startEditWorkItem(workItemByPk(component, PK_WI_A));
        component.wiValues.title = "draft of the previous session";

        window.offlineClient.getOfflineClient.mockImplementationOnce(unavailableRead);
        await component.load();
        expect.soft(component.available).toBe(false);
        expect.soft(component.data).toBe(null);
        expect.soft(component.wiValues).toEqual({});

        await component.load();

        expect.soft(component.available).toBe(true);
        expect.soft(component.data.workitems.every((w) => w.editing === false)).toBe(true);
        expect.soft(component.wiValues).toEqual({});
    });

    it("drops the open create forms as well", async () => {
        const component = await makeLoadedComponent();
        component.startCreate();
        component.createDocTypePk = "dt1";
        component.onCreateDocTypeChange();
        component.editValues.note = "draft of the previous session";
        component.creatingWorkItem = true;
        component.wiValues = { title: "draft of the previous session" };

        window.offlineClient.getOfflineClient.mockImplementationOnce(async () => null);
        await component.load();

        expect.soft(component.creating).toBe(false);
        expect.soft(component.creatingWorkItem).toBe(false);
        expect.soft(component.createDocTypePk).toBe("");
        expect.soft(component.editFields).toEqual([]);
        expect.soft(component.editValues).toEqual({});
        expect.soft(component.wiValues).toEqual({});
    });
});

// Refs #2688: a local modification keeps the server token as updated_at, so
// the token alone does not notice when a second tab of the same account
// stores a change of the same entry. Saving the still open form would then
// silently replace that unsent change.
describe("offline client view closes the form when another tab changed the entry locally", () => {
    it("closes the event form when the entry got a local modification under the same token", async () => {
        const component = await makeLoadedComponent();
        component.startEdit(eventByPk(component, PK_EVENT_A));
        component.editValues.note = "typed in this tab";
        window.offlineClient.getOfflineClient.mockImplementation(async () =>
            bundle({ noteA: "stored by the other tab", statusA: "modified" })
        );

        await component._reconcile();

        expect(eventByPk(component, PK_EVENT_A).data_fields.note).toBe("stored by the other tab");
        expect(eventByPk(component, PK_EVENT_A).editing).toBe(false);
    });

    it("closes the task form when the task got a local modification under the same token", async () => {
        const component = await makeLoadedComponent();
        component.startEditWorkItem(workItemByPk(component, PK_WI_A));
        component.wiValues.title = "typed in this tab";
        window.offlineClient.getOfflineClient.mockImplementation(async () =>
            bundle({ titleA: "stored by the other tab", statusA: "modified" })
        );

        await component._reconcile();

        expect(workItemByPk(component, PK_WI_A).title).toBe("stored by the other tab");
        expect(workItemByPk(component, PK_WI_A).editing).toBe(false);
    });

    // One open form per case: the UI never shows both at the same time.
    const OPEN_FORMS = [
        ["event", (c) => c.startEdit(eventByPk(c, PK_EVENT_A)), (c) => eventByPk(c, PK_EVENT_A)],
        ["task", (c) => c.startEditWorkItem(workItemByPk(c, PK_WI_A)), (c) => workItemByPk(c, PK_WI_A)],
    ];

    it.each(OPEN_FORMS)("closes the %s form when an already modified entry got other values", async (_kind, open, entry) => {
        const component = await makeLoadedComponent();
        window.offlineClient.getOfflineClient.mockImplementation(async () => bundle({ statusA: "modified" }));
        await component.load();
        open(component);
        window.offlineClient.getOfflineClient.mockImplementation(async () =>
            bundle({ statusA: "modified", noteA: "stored by the other tab", titleA: "stored by the other tab" })
        );

        await component._reconcile();

        expect(entry(component).editing).toBe(false);
    });

    it.each(OPEN_FORMS)("keeps the %s form of a modified entry open while its stored values stay the same", async (_kind, open, entry) => {
        const component = await makeLoadedComponent();
        window.offlineClient.getOfflineClient.mockImplementation(async () => bundle({ statusA: "modified" }));
        await component.load();
        open(component);

        await component._reconcile();
        await component._reconcile();

        expect(entry(component).editing).toBe(true);
    });
});

// Refs #2688: the view does not see every key change as a rebuild. An idle
// wipe or key expiry in this very tab only notifies other tabs, the boot gate
// adopts a key another tab derived meanwhile without refreshing the view, and
// a counter signal arriving during a running load() is dropped. In all three
// cases the next rebuild may read the same entry with the same token under a
// different sign-in, so an open form must not survive a change of the key
// epoch, whatever happened in between.
function keySession(initial) {
    let epoch = initial;
    window.crypto_session = {
        awaitReady: async () => {},
        keyEpoch: vi.fn(async () => epoch),
    };
    return {
        set(next) {
            epoch = next;
        },
    };
}

describe("offline client view binds an open form to the key it was read with", () => {
    it("drops the event form after an own-tab wipe and a new sign-in without a rebuild in between", async () => {
        const key = keySession("epoch-1");
        const component = await makeLoadedComponent();
        component.startEdit(eventByPk(component, PK_EVENT_A));
        component.editValues.note = "draft of the previous session";

        // Wipe in this tab (no event reaches the view), then a new sign-in
        // takes the same person offline; its sync signal rebuilds the view.
        key.set("epoch-2");
        window.dispatchEvent(new CustomEvent("offline-unsynced-count", { detail: { count: 0 } }));
        await flush();

        expect(window.offlineClient.getOfflineClient).toHaveBeenCalledTimes(2);
        expect(component.available).toBe(true);
        expect(component.data.events.every((e) => e.editing === false)).toBe(true);
        expect(component.editFields).toEqual([]);
        expect(component.editValues).toEqual({});
    });

    it("drops the task form after the boot gate adopted another tab's key without a rebuild", async () => {
        const key = keySession("epoch-1");
        const component = await makeLoadedComponent();
        component.startEditWorkItem(workItemByPk(component, PK_WI_A));
        component.wiValues.title = "draft of the previous session";

        key.set("epoch-2");
        await component._reconcile();

        expect(component.data.workitems.every((w) => w.editing === false)).toBe(true);
        expect(component.wiValues).toEqual({});
    });

    it("drops the form when the key changed during a load that swallowed the counter signal", async () => {
        const key = keySession("epoch-1");
        const component = await makeLoadedComponent();
        component.startEdit(eventByPk(component, PK_EVENT_A));
        component.editValues.note = "draft of the previous session";
        const pending = deferred();
        window.offlineClient.getOfflineClient.mockImplementationOnce(() => pending.promise);

        const loading = component.load();
        key.set("epoch-2");
        window.dispatchEvent(new CustomEvent("offline-unsynced-count", { detail: { count: 0 } }));
        pending.resolve(bundle());
        await loading;
        await flush();

        // The signal was dropped: no rebuild after the one in flight.
        expect(window.offlineClient.getOfflineClient).toHaveBeenCalledTimes(2);
        expect(component.data.events.every((e) => e.editing === false)).toBe(true);
        expect(component.editValues).toEqual({});
    });

    it("drops an open create form when the key changed", async () => {
        const key = keySession("epoch-1");
        const component = await makeLoadedComponent();
        component.startCreate();
        component.createDocTypePk = "dt1";
        component.onCreateDocTypeChange();
        component.editValues.note = "draft of the previous session";

        key.set("epoch-2");
        await component.load();

        expect(component.creating).toBe(false);
        expect(component.createDocTypePk).toBe("");
        expect(component.editValues).toEqual({});
    });

    it("fails closed when the key epoch cannot be read", async () => {
        const key = keySession("epoch-1");
        const component = await makeLoadedComponent();
        component.startEditWorkItem(workItemByPk(component, PK_WI_A));

        key.set(null);
        await component.load();

        expect(workItemByPk(component, PK_WI_A).editing).toBe(false);
        expect(component.wiValues).toEqual({});
    });

    it("closes the form when the crypto session offers no key epoch", async () => {
        // A stale cached crypto script without keyEpoch next to this view must
        // not switch the binding off silently.
        window.crypto_session = { awaitReady: async () => {} };
        const component = await makeLoadedComponent();
        component.startEdit(eventByPk(component, PK_EVENT_A));
        component.editValues.note = "draft of the previous session";

        await component._reconcile();

        expect(eventByPk(component, PK_EVENT_A).editing).toBe(false);
        expect(component.editValues).toEqual({});
    });

    it("keeps the form open with its input while the key stays the same", async () => {
        keySession("epoch-1");
        const component = await makeLoadedComponent();
        component.startEdit(eventByPk(component, PK_EVENT_A));
        component.editValues.note = "typed by the user";

        await component._reconcile();
        await component.load();

        expect(eventByPk(component, PK_EVENT_A).editing).toBe(true);
        expect(component.editValues.note).toBe("typed by the user");
    });
});
