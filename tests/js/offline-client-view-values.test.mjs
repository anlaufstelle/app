import { afterEach, beforeAll, expect, it, vi } from "vitest";

let factory;
beforeAll(async () => {
    window.Alpine = { addInitSelector: vi.fn(), directive: vi.fn(), data: (name, value) => { if (name === "offlineClientView") factory = value; } };
    await import("../../src/static/js/date-input-i18n.js");
    await import("../../src/static/js/offline-form-fields.js");
    await import("../../src/static/js/offline-client-view.js");
    document.dispatchEvent(new Event("alpine:init"));
});

it("renders option and translated boolean labels without changing stored values or ISO dates", async () => {
    const fields = [
        { slug: "kind", name: "Kind", field_type: "select", options: [{ value: "other", label: "Other label" }] },
        { slug: "flags", name: "Flags", field_type: "multi_select", options: [{ value: "a", label: "Option A" }] },
    ];
    const values = { kind: "other", flags: ["a", "unknown", "a"], active: false, enabled: true, date: "2026-10-14", time: "13:45", unknown: "retired", empty: null };
    const cached = { documentTypes: [{ pk: "type-1", fields }], events: [{ pk: "event-1", document_type_pk: "type-1", data_fields: values }], lastSynced: Date.now() };
    const before = JSON.parse(JSON.stringify(values));
    window.offlineClient = { getOfflineClient: vi.fn(async () => cached) };
    const component = factory();
    component.$el = document.createElement("div");
    component.$el.dataset.i18nYes = "Translated yes";
    component.$el.dataset.i18nNo = "Translated no";
    component._pk = "client-1";
    await component.load();
    expect(component.available).toBe(true);
    expect(component.data.events[0].data_fields_pairs).toEqual([
        { slug: "kind", label: "Kind", value_fmt: "Other label" },
        { slug: "flags", label: "Flags", value_fmt: "Option A\nunknown\nOption A" },
        { slug: "active", label: "active", value_fmt: "Translated no" },
        { slug: "enabled", label: "enabled", value_fmt: "Translated yes" },
        { slug: "date", label: "date", value_fmt: "2026-10-14" },
        { slug: "time", label: "time", value_fmt: "13:45" },
        { slug: "unknown", label: "unknown", value_fmt: "retired" },
        { slug: "empty", label: "empty", value_fmt: "" },
    ]);
    expect(values).toEqual(before);
});


afterEach(() => { document.documentElement.lang = ""; document.body.innerHTML = ""; });

it.each(["de", "en"])("formats offline date/time displays in app language %s without changing stored values", async language => {
    document.documentElement.lang = language;
    const values = { date: "2026-10-14", time: "13:45:17", text: "2026-10-14" };
    const cached = {
        documentTypes: [{ pk: "type-1", fields: [
            { slug: "date", name: "Date", field_type: "date" },
            { slug: "time", name: "Time", field_type: "time" },
            { slug: "text", name: "Text", field_type: "text" },
        ] }],
        events: [{ pk: "event-1", document_type_pk: "type-1", occurred_at: "2026-10-14T13:45:00", data_fields: values }],
        lastSynced: Date.now(),
    };
    const before = JSON.parse(JSON.stringify(cached));
    window.offlineClient = { getOfflineClient: vi.fn(async () => cached) };
    const component = factory();
    component.$el = document.createElement("div");
    component._pk = "client-1";
    await component.load();
    const display = language === "de" ? "14.10.2026" : "2026-10-14";
    expect(component.data.events[0].data_fields_pairs).toEqual([
        { slug: "date", label: "Date", value_fmt: display },
        { slug: "time", label: "Time", value_fmt: "13:45:17" },
        { slug: "text", label: "Text", value_fmt: "2026-10-14" },
    ]);
    expect(component.data.events[0].occurred_at_fmt).toBe(display + " 13:45");
    expect(window.offlineFormFields.formatDate("2026-10-14")).toBe(display);
    expect(window.offlineFormFields.formatTs(new Date(2026, 9, 14, 13, 45))).toBe(display + " 13:45");
    expect(values).toEqual(before.events[0].data_fields);
    expect(cached.events[0].occurred_at).toBe(before.events[0].occurred_at);
});
