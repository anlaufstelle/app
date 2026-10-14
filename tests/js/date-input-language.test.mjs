import { beforeAll, beforeEach, expect, it, vi } from "vitest";

beforeAll(async () => { await import("../../src/static/js/date-input-i18n.js"); });
beforeEach(() => { document.body.innerHTML = ""; });

it.each([
    ["de", "29.02.2024", "2024-02-29"], ["en", "2024-02-29", "2024-02-29"],
    ["de", "29.02.2025", null], ["en", "2025-02-29", null],
    ["de", "30.02.2026", null], ["en", "2026-04-31", null],
    ["de", "", ""], ["en", "", ""], ["en", "10/01/2026", null],
])("strict date parsing %s %s", (language, value, iso) => {
    expect(window.appDateFields.parse(value, "date", language)).toBe(iso);
});

it.each(["de", "en"])("preserves wall-clock seconds and fold transport in %s", language => {
    const value = language === "de" ? "25.10.2026 02:30:17" : "2026-10-25 02:30:17";
    expect(window.appDateFields.parse(value, "datetime-local", language)).toBe("2026-10-25T02:30:17");
    expect(window.appDateFields.format("2026-10-25T02:30:17", "datetime-local", language)).toBe(value);
    expect(window.appDateFields.parse("23:59:59", "time", language)).toBe("23:59:59");
    expect(window.appDateFields.parse("24:00", "time", language)).toBeNull();
});

function mount(language = "de") {
    document.body.innerHTML = `<form><label for="due">Due</label><input id="due" name="due_date" type="date"
        value="2026-10-01" required min="2026-10-01" max="2026-12-31" aria-describedby="help"
        data-date-language="${language}" data-date-placeholder="${language === "de" ? "TT.MM.JJJJ" : "YYYY-MM-DD"}"
        data-date-invalid="Invalid date" data-date-required="Required" data-date-range="Out of range"
        data-msg-too-early="Too early"><span id="help">Help</span></form>`;
    return window.appDateFields.enhance(document.querySelector("input"));
}

it.each(["de", "en"])("keeps labels, constraints and ISO form submission in %s", language => {
    const field = mount(language);
    expect(document.getElementById("due")).toBe(field.visible);
    expect(field.visible.getAttribute("aria-describedby")).toBe("help");
    expect(field.visible.required).toBe(true);
    expect(field.visible.min).toBe("2026-10-01");
    expect(field.visible.value).toBe(language === "de" ? "01.10.2026" : "2026-10-01");
    expect(new FormData(document.querySelector("form")).get("due_date")).toBe("2026-10-01");
    field.visible.value = language === "de" ? "30.02.2026" : "2026-02-30";
    field.visible.dispatchEvent(new Event("input", { bubbles: true }));
    expect(field.visible.validationMessage).toBe("Invalid date");
    expect(field.transport.value).toBe("");
    expect(window.appDateFields.validate(document.querySelector("form"))).toBe(false);
    field.visible.value = language === "de" ? "30.09.2026" : "2026-09-30";
    field.visible.dispatchEvent(new Event("input", { bubbles: true }));
    expect(field.visible.validationMessage).toBe("Too early");
});

it("updates offline ISO x-model through input events without network", () => {
    const field = mount();
    const values = { due_date: field.transport.value };
    field.transport.addEventListener("input", () => { values.due_date = field.transport.value; });
    const fetch = vi.spyOn(window, "fetch");
    field.visible.value = "14.10.2026";
    field.visible.dispatchEvent(new Event("input", { bubbles: true }));
    expect(values.due_date).toBe("2026-10-14");
    expect(fetch).not.toHaveBeenCalled();
    fetch.mockRestore();
});

it("retains an invalid server re-render as visible text and blocks submission", () => {
    document.body.innerHTML = '<form><input name="date_from" type="date" value="2026-02-30" data-date-language="de" data-date-invalid="Invalid date"></form>';
    const transport = document.querySelector("input");
    // Browsers sanitize invalid native dates; jsdom currently retains them.
    transport.value = "";
    const field = window.appDateFields.enhance(transport);
    expect(field.visible.value).toBe("30.02.2026");
    expect(field.visible.validationMessage).toBe("Invalid date");
    expect(window.appDateFields.validate(document.querySelector("form"))).toBe(false);
});

it("preserves native fallback if Alpine never starts", () => {
    document.body.innerHTML = '<form><input type="date" name="date_from" value="2026-10-01" x-app-date></form>';
    expect(document.querySelector("input").type).toBe("date");
    expect(new FormData(document.querySelector("form")).get("date_from")).toBe("2026-10-01");
});

it("keeps step validation stable after successive edits and restores reset values", async () => {
    document.body.innerHTML = '<form><input type="time" name="clock" value="12:00" step="60" data-date-language="en" data-date-step="Minute precision"></form>';
    const field = window.appDateFields.enhance(document.querySelector("input"));
    field.visible.value = "12:01";
    field.visible.dispatchEvent(new Event("input", { bubbles: true }));
    field.visible.value = "12:01:17";
    field.visible.dispatchEvent(new Event("input", { bubbles: true }));
    expect(field.visible.validationMessage).toBe("Minute precision");
    document.querySelector("form").reset();
    await new Promise(resolve => queueMicrotask(resolve));
    expect(field.visible.value).toBe("12:00");
    expect(field.transport.value).toBe("12:00");
});


it.each(["de", "en"])("rewires a serialized htmx history snapshot in %s", language => {
    const original = mount(language);
    original.visible.value = language === "de" ? "14.10.2026" : "2026-10-14";
    original.visible.dispatchEvent(new Event("input", { bubbles: true }));
    const snapshot = document.body.innerHTML;
    original.destroy();
    document.body.innerHTML = snapshot;
    const restored = window.appDateFields.enhance(document.querySelector("input[name=due_date]"));
    expect(restored).not.toBeNull();
    expect(document.querySelectorAll("[data-app-date-visible]")).toHaveLength(1);
    expect(document.getElementById("due")).toBe(restored.visible);
    expect(restored.visible.value).toBe(language === "de" ? "14.10.2026" : "2026-10-14");
    restored.visible.value = language === "de" ? "15.10.2026" : "2026-10-15";
    restored.visible.dispatchEvent(new Event("input", { bubbles: true }));
    expect(new FormData(document.querySelector("form")).get("due_date")).toBe("2026-10-15");
});

it("reinitializes after cleanup without duplicate reset listeners", async () => {
    const first = mount();
    const transport = first.transport;
    first.destroy();
    const second = window.appDateFields.enhance(transport);
    expect(second.visible.isConnected).toBe(true);
    expect(window.appDateFields.enhance(transport)).toBe(second);
    const onInput = vi.fn();
    transport.addEventListener("input", onInput);
    document.querySelector("form").reset();
    await new Promise(resolve => queueMicrotask(resolve));
    expect(onInput).toHaveBeenCalledTimes(1);
    expect(second.visible.value).toBe("01.10.2026");
});


it.each([false, true])("respects form noValidate=%s when submitting invalid dates", noValidate => {
    const field = mount();
    const form = document.querySelector("form");
    form.noValidate = noValidate;
    field.visible.value = "";
    field.visible.dispatchEvent(new Event("input", { bubbles: true }));
    const submit = new Event("submit", { bubbles: true, cancelable: true });
    form.dispatchEvent(submit);
    expect(submit.defaultPrevented).toBe(!noValidate);
    expect(field.transport.value).toBe("");
});
