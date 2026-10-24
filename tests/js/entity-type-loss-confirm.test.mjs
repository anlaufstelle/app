import { afterEach, beforeAll, expect, it, vi } from "vitest";

const factories = {};
beforeAll(async () => {
    window.Alpine = { data: (name, factory) => { factories[name] = factory; } };
    await import("../../src/static/js/alpine/forms.js");
    document.dispatchEvent(new Event("alpine:init"));
});
afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
});

it.each([true, false])("records explicit type-loss consent only when accepted: %s", (accepted) => {
    document.body.innerHTML = `<form data-original-document-type="old" data-confirm-discard="Discard one value?">
        <select name="document_type"><option value="new" selected>New</option></select>
        <input type="hidden" name="confirm_data_loss" value="">
        <div id="dynamic-fields"></div>
    </form>`;
    const form = document.querySelector("form");
    const component = factories.confirmDynamicDataLoss();
    component.$el = form;
    component.init();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(accepted);
    const event = { target: form, preventDefault: vi.fn() };
    component.confirmDiscard(event);
    expect(confirm).toHaveBeenCalledWith("Discard one value?");
    expect(form.elements.confirm_data_loss.value).toBe(accepted ? "1" : "");
    expect(event.preventDefault).toHaveBeenCalledTimes(accepted ? 0 : 1);
});

it("clears prior consent when the original type is retained", () => {
    document.body.innerHTML = `<form data-original-document-type="old" data-confirm-discard="Discard?">
        <select name="document_type"><option value="old" selected>Old</option></select>
        <input type="hidden" name="confirm_data_loss" value="1">
    </form>`;
    const form = document.querySelector("form");
    const component = factories.confirmDynamicDataLoss();
    component.$el = form;
    component.init();
    const confirm = vi.spyOn(window, "confirm");
    component.confirmDiscard({ target: form, preventDefault: vi.fn() });
    expect(confirm).not.toHaveBeenCalled();
    expect(form.elements.confirm_data_loss.value).toBe("");
});
