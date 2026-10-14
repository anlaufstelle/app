import { beforeAll, afterEach, describe, expect, it, vi } from "vitest";

const factories = {};
beforeAll(async () => {
    window.Alpine = { data: (name, factory) => { factories[name] = factory; } };
    await import("../../src/static/js/alpine/forms.js");
    document.dispatchEvent(new Event("alpine:init"));
});
afterEach(() => vi.unstubAllGlobals());
function component(name = "eventClientAutocomplete") {
    const c = factories[name]();
    c._casesForClientUrl = "/cases/";
    c._autocompleteUrl = "/search/";
    c.selectedId = "a";
    return c;
}
const ok = (data) => ({ ok: true, json: async () => data });
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

it("template navigation carries the current selection", () => {
    const c = component();
    c.selectedId = "b";
    c.selectedCaseId = "current-case";
    const link = document.createElement("a");
    link.href = "/events/create/?template=preset&client=a&case=old";
    c.updateContextLink({ currentTarget: link });
    const url = new URL(link.href);
    expect(url.searchParams.get("client")).toBe("b");
    expect(url.searchParams.get("case")).toBe("current-case");
});
