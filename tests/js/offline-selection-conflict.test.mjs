import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

let factory;
let record;
const options = [
    { value: "a", label: "First label" },
    { value: "b", label: "Second label" },
    { value: "c", label: "Inactive label" },
    { value: "comma,value", label: "Comma, label" },
    { value: "quote'value", label: "Quoted 'label'" },
];

beforeAll(async () => {
    window.Alpine = { data: (name, build) => { if (name === "conflictResolver") factory = build; } };
    await import("../../src/static/js/offline-form-fields.js");
    await import("../../src/static/js/conflict-resolver.js");
    document.dispatchEvent(new Event("alpine:init"));
});

beforeEach(() => {
    record = {
        pk: "selection-event",
        localStatus: "conflict",
        data: {
            formData: { selection: ["b", "a", "b"], other: ["a"] },
            serverState: {
                data_json: { selection: ["a", "c"], other: ["c"] },
                field_options: { selection: options, other: options },
                updated_at: "2026-10-09T10:00:00+00:00",
            },
        },
    };
    window.offlineStore = {
        getOfflineEvent: vi.fn(async () => JSON.parse(JSON.stringify(record))),
        saveOfflineEdit: vi.fn(async (saved) => { record = JSON.parse(JSON.stringify(saved)); }),
        clearOfflineEdit: vi.fn(async () => {}),
    };
    window.offlineEdit = { replayModifiedEvent: vi.fn(async () => ({ status: "synced" })) };
});

describe("Selection conflict rendering and resolution", () => {
    it("renders each label on its own line without changing the raw arrays", async () => {
        const component = factory();
        component.eventPk = record.pk;
        await component.load();
        expect(component.error).toBe("");
        expect(component.diffRows.find((row) => row.key === "selection")).toMatchObject({
            localFmt: "Second label\nFirst label\nSecond label",
            serverFmt: "First label\nInactive label",
        });
        expect(component.localData.selection).toEqual(["b", "a", "b"]);
        expect(component.serverData.selection).toEqual(["a", "c"]);
    });

    it("formats unknown slugs, empty arrays and text without JSON heuristics", () => {
        const format = window.offlineFormFields.formatValue;
        expect(format(["comma,value", "quote'value", "unknown"], options)).toBe("Comma, label\nQuoted 'label'\nunknown");
        expect(format([], options)).toBe("");
        expect(format(["unknown"])).toBe("unknown");
        expect(format('["a","b"]', options)).toBe('["a","b"]');
        expect(format({ key: ["a"] }, options)).toBe('{"key":["a"]}');
    });

    it.each(["local", "server", "mixed"])("replays raw arrays for the %s choice with the fresh token", async (choice) => {
        const component = factory();
        component.eventPk = record.pk;
        await component.load();
        component.perFieldChoice = { selection: choice === "server" ? "server" : "local", other: "server" };
        if (choice === "local") await component.keepLocal();
        else await component.keepMerged();
        const replayed = window.offlineEdit.replayModifiedEvent.mock.calls[0][0];
        expect(replayed.localStatus).toBe("modified");
        expect(replayed.data.expectedUpdatedAt).toBe("2026-10-09T10:00:00+00:00");
        expect(replayed.data.formData.selection).toEqual(choice === "server" ? ["a", "c"] : ["b", "a", "b"]);
        expect(replayed.data.formData.other).toEqual(choice === "local" ? ["a"] : ["c"]);
        expect(Array.isArray(replayed.data.formData.selection)).toBe(true);
        expect(JSON.stringify(replayed.data.formData)).not.toContain("label");
        expect(component.resolved).toBe(true);
    });

    it("keeps the server by discarding the local edit without posting labels", async () => {
        const component = factory();
        component.eventPk = record.pk;
        await component.load();
        await component.keepServer();
        expect(window.offlineStore.clearOfflineEdit).toHaveBeenCalledWith(record.pk);
        expect(window.offlineEdit.replayModifiedEvent).not.toHaveBeenCalled();
        expect(component.resolved).toBe(true);
    });
});


describe("Inherited field slugs", () => {
    const slugs = ["constructor", "__proto__", "toString", "hasOwnProperty"];

    it.each(slugs)("keeps the orphan conflict form visible for %s", async (slug) => {
        record.data.formData = {};
        record.data.serverState.data_json = { [slug]: ["a", "b", "a"] };
        record.data.serverState.field_options = {};
        const component = factory();
        component.eventPk = record.pk;
        await component.load();
        expect(component.showForm).toBe(true);
        expect(component.error).toBe("");
        expect(component.diffRows).toEqual([{ key: slug, localFmt: "", serverFmt: "a\nb\na", hasDiff: true, diffClass: "bg-yellow-50" }]);
        expect(component.serverData[slug]).toEqual(["a", "b", "a"]);
    });

    it.each(slugs)("ignores inherited label metadata for %s", async (slug) => {
        record.data.formData = { [slug]: ["a"] };
        record.data.serverState.data_json = { [slug]: ["b"] };
        record.data.serverState.field_options = Object.create({ [slug]: options });
        window.offlineStore.getOfflineEvent = vi.fn(async () => record);
        const component = factory();
        component.eventPk = record.pk;
        await component.load();
        expect(component.showForm).toBe(true);
        expect(component.diffRows[0]).toMatchObject({ localFmt: "a", serverFmt: "b" });
    });

    it.each([null, {}, "invalid", 7])("falls back to raw slugs for non-array metadata %j", async (metadata) => {
        record.data.serverState.field_options.selection = metadata;
        const component = factory();
        component.eventPk = record.pk;
        await component.load();
        expect(component.showForm).toBe(true);
        expect(component.diffRows.find((row) => row.key === "selection")).toMatchObject({ localFmt: "b\na\nb", serverFmt: "a\nc" });
    });

    it.each(slugs)("uses own option metadata and replays the chosen array for %s", async (slug) => {
        record.data.formData = { [slug]: ["b", "a", "b"] };
        record.data.serverState.data_json = { [slug]: ["a", "c"] };
        record.data.serverState.field_options = { [slug]: options };
        const component = factory();
        component.eventPk = record.pk;
        await component.load();
        expect(component.showForm).toBe(true);
        expect(component.diffRows[0]).toMatchObject({ localFmt: "Second label\nFirst label\nSecond label", serverFmt: "First label\nInactive label" });
        component.perFieldChoice[slug] = "server";
        await component.keepMerged();
        const replayed = window.offlineEdit.replayModifiedEvent.mock.calls[0][0];
        expect(JSON.parse(JSON.stringify(replayed.data.formData))).toEqual({ [slug]: ["a", "c"] });
        expect(component.resolved).toBe(true);
    });
});
