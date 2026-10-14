import { readFileSync } from "node:fs";
import Alpine from "@alpinejs/csp";
import { beforeAll, afterEach, describe, expect, it, vi } from "vitest";

const factories = {};
let template;
beforeAll(async () => {
    template = readFileSync("src/templates/components/_client_autocomplete.html", "utf8");
    window.Alpine = Alpine;
    const register = Alpine.data.bind(Alpine);
    vi.spyOn(Alpine, "data").mockImplementation((name, factory) => {
        factories[name] = factory;
        register(name, factory);
    });
    await import("../../src/static/js/alpine/forms.js");
    document.dispatchEvent(new Event("alpine:init"));
});
afterEach(() => {
    Alpine.destroyTree(document.body);
    document.body.innerHTML = "";
    vi.unstubAllGlobals();
});
const ok = (data) => ({ ok: true, json: async () => data });
const results = [{ id: "a", pseudonym: "Person A", stage: "Stage" }, { id: "b", pseudonym: "Person B" }];
function component(name) {
    const c = factories[name]();
    c._autocompleteUrl = "/search/";
    c.query = "Person";
    c._applyResults(results);
    c._enrich();
    return c;
}

function render() {
    const field = template.match(/<input type="text"[\s\S]*?autocomplete="off">/)[0].replace(/{%[\s\S]*?%}/g, "");
    const list = template.match(/<div x-show="hasVisibleResults"[\s\S]*?<\/template>\s*<\/div>/)[0];
    const retry = template.match(/<button type="button" @click="fetchResults"[\s\S]*?<\/button>/)[0];
    document.body.innerHTML = '<div x-data="clientAutocomplete" data-autocomplete-url="/search/">' + field + list + retry + '</div>';
    Alpine.initTree(document.body);
    return { c: Alpine.$data(document.body.firstElementChild), input: document.querySelector('input[type="text"]') };
}

it("preserves the existing primary listbox ID at runtime", () => {
    render();
    expect(document.querySelector('[role="listbox"]').id).toBe("client-autocomplete-list");
});
it("selects exactly once through click alone and never through mousedown alone", async () => {
    const { c, input } = render();
    c._justSelected = true;
    input.focus();
    c._applyResults(results);
    await Alpine.nextTick();
    const select = vi.fn();
    const original = c.selectItem;
    c.selectItem = function (item) { select(item); original.call(this, item); };
    const button = document.querySelector('[role="option"]');
    button.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
    expect(c.selectedId).toBe("");
    expect(select).not.toHaveBeenCalled();
    expect(button.dataset.idx).toBe("0");
    // Simulate assistive activation of an explicitly focused result.
    button.focus();
    button.click();
    expect(select).toHaveBeenCalledOnce();
    expect(c.selectedId).toBe("a");
    expect(document.activeElement).toBe(input);
});
it("removes the actual active descendant attribute after Escape", async () => {
    const { c, input } = render();
    c._applyResults(results);
    await Alpine.nextTick();
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
    await Alpine.nextTick();
    expect(input.getAttribute("aria-activedescendant")).toBe(document.querySelector('[role="option"]').id);
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await Alpine.nextTick();
    expect(input.hasAttribute("aria-activedescendant")).toBe(false);
});
it("preserves retry when the click also reaches the input's click-away handler", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok(results)));
    const { c, input } = render();
    Object.defineProperty(input, "offsetWidth", { value: 100 });
    Object.defineProperty(input, "offsetHeight", { value: 20 });
    c.searchState = "error";
    Array.from(document.querySelectorAll("button")).find((button) => button.getAttribute("@click") === "fetchResults").click();
    await vi.waitFor(() => expect(c.searchState).toBe("success"));
    expect(c.results).toEqual(results);
});

describe.each(["clientAutocomplete", "eventClientAutocomplete"])("%s active option", (name) => {
    it("reports the highlighted option and gives every instance unique IDs", () => {
        const c = component(name);
        const other = component(name);
        c.moveHighlightDown();
        c._enrich();
        expect(c.activeDescendant).toBe(c.enrichedResults[0].optionId);
        expect(typeof c.activeDescendant).toBe("string");
        expect(c.activeDescendant).not.toBe(other.enrichedResults[0].optionId);
    });
    it.each(["hide", "select", "reset", "empty", "new", "error"])("removes the active reference on %s", async (action) => {
        const c = component(name);
        c.moveHighlightDown();
        if (action === "hide") c.hideResults();
        if (action === "select") c.selectItem(results[0]);
        if (action === "reset") c.resetSearch();
        if (action === "empty") c._applyResults([]);
        if (action === "new") c._applyResults([{ id: "new" }]);
        if (action === "error") {
            vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Offline")));
            await c.fetchResults();
        }
        expect(c.activeDescendant).toBeNull();
    });
    it("keeps the popup closed when a pending debounce fires after selection", async () => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok(results)));
        const c = component(name);
        c.selectItem(results[0]);
        await c.onInput();
        await Promise.resolve();
        expect(fetch).not.toHaveBeenCalled();
        expect(c.show).toBe(false);
        expect(c.activeDescendant).toBeNull();
    });
    it("cannot confirm an invisible option after Escape", () => {
        const c = component(name);
        c.moveHighlightDown();
        c.hideResults();
        c.confirmHighlight();
        expect(c.selectedId).toBe("");
    });
});
