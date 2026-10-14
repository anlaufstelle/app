import { beforeAll, expect, it } from "vitest";

const settle = () => new Promise(resolve => setTimeout(resolve, 20));
beforeAll(async () => {
    await import("../../src/static/js/date-input-i18n.js");
    // Exercise the shipped CSP build; no expression evaluator or mocked x-model.
    await import("../../src/static/js/alpine/forms.js");
    await import("../../src/static/js/alpine-csp.min.js");
    await settle();
});

it("initializes standalone inputs and updates ISO values in the actual CSP runtime", async () => {
    document.body.innerHTML = `<form><label for="standalone">Date</label><input id="standalone" name="date_from"
        type="date" x-app-date data-date-language="de" data-date-placeholder="TT.MM.JJJJ" value="2026-10-01"></form>`;
    await settle();
    const visible = document.getElementById("standalone");
    expect(visible.type).toBe("text");
    expect(visible.value).toBe("01.10.2026");
    visible.value = "14.10.2026";
    visible.dispatchEvent(new Event("input", { bubbles: true }));
    expect(new FormData(document.querySelector("form")).get("date_from")).toBe("2026-10-14");
});

it("keeps reactive offline values in ISO when a field changes or resets", async () => {
    window.Alpine.data("dateTest", () => ({ value: "2026-10-01", minimum: "2026-10-01", required: true }));
    document.body.innerHTML = `<form x-data="dateTest"><label for="offline">Date</label><input id="offline"
        type="date" x-app-date x-model="value" :min="minimum" :required="required"
        data-date-language="de" data-date-placeholder="TT.MM.JJJJ"></form>`;
    await settle();
    const form = document.querySelector("form");
    const component = window.Alpine.$data(form);
    const visible = document.getElementById("offline");
    expect(visible.value).toBe("01.10.2026");
    expect(visible.required).toBe(true);
    visible.value = "14.10.2026";
    visible.dispatchEvent(new Event("input", { bubbles: true }));
    await settle();
    expect(component.value).toBe("2026-10-14");
    component.value = "2026-10-25";
    component.minimum = "2026-10-20";
    await settle();
    expect(visible.value).toBe("25.10.2026");
    expect(visible.min).toBe("2026-10-20");
    visible.value = "30.02.2026";
    visible.dispatchEvent(new Event("input", { bubbles: true }));
    await settle();
    expect(component.value).toBe("");
    expect(visible.value).toBe("30.02.2026");
    expect(window.appDateFields.validate(form)).toBe(false);
});

it("enhances dynamic time fields with seconds, preserving labels and required state", async () => {
    window.Alpine.data("dynamicDateTest", () => ({ field: { inputType: "time", value: "13:45:17", isRequired: true, slug: "clock" } }));
    document.body.innerHTML = `<form x-data="dynamicDateTest"><label :for="field.slug">Time</label>
        <input :type="field.inputType" :id="field.slug" x-model="field.value" :required="field.isRequired"
        step="1" x-app-date data-date-language="en" data-date-placeholder="YYYY-MM-DD" data-date-time-placeholder="HH:MM:SS"></form>`;
    await settle();
    const visible = document.querySelector('[data-app-date-visible]');
    expect(visible.id).toBe("clock");
    expect(visible.placeholder).toBe("HH:MM:SS");
    expect(visible.value).toBe("13:45:17");
    expect(visible.required).toBe(true);
    visible.value = "23:59:58";
    visible.dispatchEvent(new Event("input", { bubbles: true }));
    await settle();
    expect(window.Alpine.$data(document.querySelector("form")).field.value).toBe("23:59:58");
});

it.each(["de", "en"])("date shortcuts retain application display and ISO submission in %s", async language => {
    document.body.innerHTML = `<form><input id="shortcut" name="due_date" type="date" x-app-date data-date-language="${language}">
        <div x-data="dateQuickButtons" data-target-input="shortcut"></div></form>`;
    await settle();
    const buttons = document.querySelector("[x-data]");
    window.Alpine.$data(buttons)._commit(new Date(2026, 9, 14));
    await settle();
    expect(document.getElementById("shortcut").value).toBe(language === "de" ? "14.10.2026" : "2026-10-14");
    expect(new FormData(document.querySelector("form")).get("due_date")).toBe("2026-10-14");
});
