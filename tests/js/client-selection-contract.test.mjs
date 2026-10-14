import { beforeAll, afterEach, describe, expect, it, vi } from "vitest";

const factories = {};
beforeAll(async () => {
    window.Alpine = { data: (name, factory) => { factories[name] = factory; } };
    await import("../../src/static/js/alpine/forms.js");
    document.dispatchEvent(new Event("alpine:init"));
});
afterEach(() => vi.unstubAllGlobals());
const ok = (data) => ({ ok: true, json: async () => data });
function component(name, dataset = {}) {
    const c = factories[name]();
    c.$el = { dataset: { autocompleteUrl: "/search/", ...dataset } };
    c.$refs = {};
    // Alpine watchers run later; immediate assertions must not depend on them.
    c.$watch = vi.fn();
    c.init();
    return c;
}

describe.each(["clientAutocomplete", "eventClientAutocomplete"])("%s selection", (name) => {
    it.each(["Person B", "", "Person A"])("invalidates the ID synchronously on real input %j", (text) => {
        const c = component(name);
        c.selectItem({ id: "a", pseudonym: "Person A" });
        c.setQuery({ target: { value: text } });
        expect(c.selectedId).toBe("");
        expect(c.query).toBe(text);
        expect(Boolean(c.unconfirmedQuery)).toBe(Boolean(text));
    });
    it("searches on the first input after selection", async () => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok([])));
        const c = component(name);
        c.selectItem({ id: "a", pseudonym: "Person A" });
        c.setQuery({ target: { value: "Person B" } });
        await c.onInput();
        expect(fetch).toHaveBeenCalledOnce();
        expect(decodeURIComponent(fetch.mock.calls[0][0]).replaceAll("+", " ")).toContain("q=Person B");
    });
    it("preserves a server prefill and an inline selection until input", () => {
        const c = component(name, { initialClientId: "a", initialPseudonym: "Person A" });
        expect(c.selectedId).toBe("a");
        expect(c.query).toBe("Person A");
        expect(Boolean(c.unconfirmedQuery)).toBe(false);
        c.selectItem({ id: "inline", pseudonym: "Inline person" });
        expect(c.selectedId).toBe("inline");
        expect(c.query).toBe("Inline person");
        expect(Boolean(c.unconfirmedQuery)).toBe(false);
        c.setQuery({ target: { value: "Inline person" } });
        expect(c.selectedId).toBe("");
        expect(Boolean(c.unconfirmedQuery)).toBe(true);
    });
    it("discards a search response between input and debounce", async () => {
        let finish;
        vi.stubGlobal("fetch", vi.fn(() => new Promise((resolve) => { finish = resolve; })));
        const c = component(name);
        c.query = "old";
        const request = c.fetchResults();
        c.setQuery({ target: { value: "new" } });
        finish(ok([{ id: "old", pseudonym: "Old result" }]));
        await request;
        expect(c.results).toEqual([]);
    });
});

it.each(["Person B", ""])("clears event case intent before debounce and rejects late cases on %j", async (text) => {
    let finish;
    vi.stubGlobal("fetch", vi.fn(() => new Promise((resolve) => { finish = resolve; })));
    const c = component("eventClientAutocomplete");
    c._casesForClientUrl = "/cases/";
    c.selectedId = "a";
    c._initialCaseId = "a-case";
    const request = c.loadCasesForClient("a");
    c.clientCases = [{ id: "a-case" }];
    c.selectedCaseId = "a-case";
    c.setQuery({ target: { value: text } });
    expect(c.selectedId).toBe("");
    expect(c.clientCases).toEqual([]);
    expect(c.selectedCaseId).toBe("");
    expect(c._initialCaseId).toBe("");
    finish(ok([{ id: "a-case" }]));
    await request;
    expect(c.clientCases).toEqual([]);
    expect(c.selectedCaseId).toBe("");
});
