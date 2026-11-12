import Alpine from "@alpinejs/csp";
import { beforeAll, afterEach, describe, expect, it, vi } from "vitest";

// Characterises the shared person-autocomplete contract of both Alpine
// components: keyboard flow, ARIA state, selection transitions, error state,
// instance isolation and getter reactivity. Real Alpine is used for the
// reactivity checks; everything else calls the factories directly.
const factories = {};
beforeAll(async () => {
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

const NAMES = ["clientAutocomplete", "eventClientAutocomplete"];
const ok = (data) => ({ ok: true, json: async () => data });
const people = [
    { id: "a", pseudonym: "Person A" },
    { id: "b", pseudonym: "Person B" },
    { id: "c", pseudonym: "Person C" },
];
function component(name, dataset = {}) {
    const c = factories[name]();
    c.$el = { dataset: { autocompleteUrl: "/search/", ...dataset } };
    c.$refs = {};
    c.$watch = vi.fn();
    c.init();
    return c;
}
function open(c, items = people) {
    c.query = "Per";
    c._applyResults(items);
    c._enrich();
}
const GETTERS = ["unconfirmedQuery", "activeDescendant", "ariaExpanded", "hasVisibleResults", "noMatches", "showMinStageHint", "minStageHint"];

describe.each(NAMES)("%s keyboard flow", (name) => {
    it("moves down with wrap-around and up with wrap-around", () => {
        const c = component(name);
        open(c);
        const seen = [];
        for (const step of ["Down", "Down", "Down", "Down", "Up", "Up"]) {
            c[`moveHighlight${step}`]();
            seen.push(c.highlightIndex);
        }
        expect(seen).toEqual([0, 1, 2, 0, 2, 1]);
    });
    it("ignores arrows while the list is closed or empty", () => {
        const c = component(name);
        open(c);
        c.hideResults();
        c.moveHighlightDown();
        expect(c.highlightIndex).toBe(-1);
        const empty = component(name);
        open(empty, []);
        empty.show = true;
        empty.moveHighlightUp();
        expect(empty.highlightIndex).toBe(-1);
    });
    it("confirms the highlighted option with Enter and nothing without highlight", () => {
        const c = component(name);
        open(c);
        c.confirmHighlight();
        expect(c.selectedId).toBe("");
        c.moveHighlightDown();
        c.moveHighlightDown();
        c.confirmHighlight();
        expect(c.selectedId).toBe("b");
        expect(c.query).toBe("Person B");
        expect(c.show).toBe(false);
        expect(c.highlightIndex).toBe(-1);
    });
    it("closes with Escape and drops the highlight", () => {
        const c = component(name);
        open(c);
        c.moveHighlightDown();
        c.hideResults();
        expect(c.show).toBe(false);
        expect(c.highlightIndex).toBe(-1);
        expect(c.ariaExpanded).toBe("false");
    });
    it("highlights and selects by data-idx", () => {
        const c = component(name);
        open(c);
        c.highlightByEvent({ currentTarget: { dataset: { idx: "1" } } });
        expect(c.highlightIndex).toBe(1);
        c.$refs = { clientInput: { focus: vi.fn() } };
        c.selectByEvent({ currentTarget: { dataset: { idx: "2" } } });
        expect(c.selectedId).toBe("c");
        expect(c.$refs.clientInput.focus).toHaveBeenCalledOnce();
    });
});

describe.each(NAMES)("%s ARIA state", (name) => {
    it("exposes expanded, visible, no-match and active-descendant state", () => {
        const c = component(name);
        expect(c.ariaExpanded).toBe("false");
        expect(c.hasVisibleResults).toBe(false);
        open(c);
        expect(c.ariaExpanded).toBe("true");
        expect(c.hasVisibleResults).toBe(true);
        expect(c.noMatches).toBe(false);
        expect(c.activeDescendant).toBeNull();
        c.moveHighlightDown();
        c._enrich();
        expect(c.activeDescendant).toBe(`${c.listboxId}-option-0`);
        expect(c.enrichedResults.map((r) => r.optionId)).toEqual([0, 1, 2].map((i) => `${c.listboxId}-option-${i}`));
        expect(c.enrichedResults.map((r) => r.isHighlighted)).toEqual([true, false, false]);
        expect(c.enrichedResults.map((r) => r.highlightClass)).toEqual(["bg-accent-light", "", ""]);
        open(c, []);
        expect(c.hasVisibleResults).toBe(false);
        expect(c.noMatches).toBe(true);
    });
    it("pairs the input id with the listbox id", () => {
        const c = component(name);
        expect(c.inputId).toBe(c.listboxId.replace("-list", "-input"));
    });
    it("registers watchers that keep the enriched options in sync", () => {
        const c = component(name);
        expect(c.$watch.mock.calls.map(([key]) => key).sort()).toEqual(
            name === "eventClientAutocomplete" ? ["highlightIndex", "results", "selectedId"] : ["highlightIndex", "results"],
        );
    });
});

describe.each(NAMES)("%s selection transitions", (name) => {
    it("selects, changes and clears while keeping the hidden ID in step", () => {
        const events = [];
        window.addEventListener("client-selection-changed", () => events.push("changed"));
        const c = component(name);
        open(c);
        c.selectItem(people[0]);
        expect([c.selectedId, c.query, c.show, c.searched, c.results, c.highlightIndex]).toEqual(["a", "Person A", false, false, [], -1]);
        expect(c.selectionCleared).toBe(false);
        expect(c.unconfirmedQuery).toBe(false);
        c.setQuery({ target: { value: "Person Ax" } });
        expect([c.selectedId, c.query, c.unconfirmedQuery]).toEqual(["", "Person Ax", true]);
        c.setSelectedId({ target: { value: "z" } });
        expect(c.selectedId).toBe("z");
        c.setQuery({ target: { value: "" } });
        expect([c.selectedId, c.unconfirmedQuery]).toEqual(["", false]);
        expect(events.length).toBe(4);
    });
    it("reads the server prefill and the autocomplete URL from data attributes", () => {
        const c = component(name, { initialClientId: "x", initialPseudonym: "Person X", minStageHint: "Needs %(stage)s" });
        expect([c.selectedId, c.query, c._autocompleteUrl]).toEqual(["x", "Person X", "/search/"]);
        c.minStageLabel = "Qualified";
        expect([c.showMinStageHint, c.minStageHint]).toEqual([true, "Needs Qualified"]);
    });
    it("starts a search with q, shows loading and then success or error", async () => {
        const c = component(name);
        c.query = "Per son";
        let finish;
        vi.stubGlobal("fetch", vi.fn(() => new Promise((resolve) => { finish = resolve; })));
        const request = c.fetchResults();
        expect([c.searchState, c.show]).toEqual(["loading", true]);
        expect(decodeURIComponent(fetch.mock.calls[0][0]).replaceAll("+", " ")).toBe("/search/?q=Per son");
        finish(ok(people));
        await request;
        expect([c.searchState, c.results.length]).toEqual(["success", 3]);
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
        await c.fetchResults();
        expect([c.searchState, c.results, c.noMatches]).toEqual(["error", [], false]);
    });
    it("opens a search on focus only when closed and not just selected", () => {
        vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
        const c = component(name);
        c.selectItem(people[0]);
        c.onFocus();
        expect(fetch).not.toHaveBeenCalled();
        c.setQuery({ target: { value: "P" } });
        c.onFocus();
        expect(fetch).toHaveBeenCalledOnce();
    });
});

describe("minimum contact level and anonymous handling", () => {
    const select = (minStage, label = "") => ({
        options: [{ dataset: { minStage, minStageLabel: label } }],
        selectedIndex: 0,
    });
    it("sends min_stage only from the event variant and only when set", () => {
        const plain = component("clientAutocomplete");
        plain.query = "Ab";
        expect(plain.buildAutocompleteUrl()).toBe("/search/?q=Ab");
        const event = component("eventClientAutocomplete");
        event.query = "Ab";
        expect(event.buildAutocompleteUrl()).toBe("/search/?q=Ab");
        event._updateStageInfo(select("identified", "Identified"));
        expect(event.buildAutocompleteUrl()).toBe("/search/?q=Ab&min_stage=identified");
    });
    it("derives the anonymous flags from the chosen document type", () => {
        const c = component("eventClientAutocomplete");
        expect([c.anonymousAllowed, c.notAnonymousAllowed, c.showAnonymousHint]).toEqual([true, false, true]);
        c._updateStageInfo(select("qualified", "Qualified"));
        expect([c.anonymousAllowed, c.notAnonymousAllowed, c.showAnonymousHint]).toEqual([false, true, false]);
        expect([c.minStage, c.minStageLabel]).toEqual(["qualified", "Qualified"]);
    });
    it("clears a selected person and says so when the type raises the level", () => {
        const c = component("eventClientAutocomplete");
        c.selectItem(people[0]);
        c.updateAnonymousAllowed(select("identified", "Identified"));
        expect([c.selectedId, c.query, c.selectionCleared, c.show]).toEqual(["", "", true, false]);
        c.updateAnonymousAllowed(select("", ""));
        expect(c.selectionCleared).toBe(false);
    });
    it("keeps a selection when the type needs no level", () => {
        const c = component("eventClientAutocomplete");
        c.selectItem(people[0]);
        c.updateAnonymousAllowed(select("", ""));
        expect([c.selectedId, c.selectionCleared]).toEqual(["a", false]);
    });
});

describe.each(NAMES)("%s instance isolation", (name) => {
    it("shares no arrays, generations or selection between instances", async () => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok(people)));
        const a = component(name);
        const b = component(name);
        expect(a.listboxId).not.toBe(b.listboxId);
        expect(a.inputId).not.toBe(b.inputId);
        expect(a.results).not.toBe(b.results);
        expect(a.enrichedResults).not.toBe(b.enrichedResults);
        a.query = "Per";
        await a.fetchResults();
        a.selectItem(people[1]);
        expect(a._fetchGen).toBeGreaterThan(0);
        expect([b.results, b.selectedId, b.query, b._fetchGen, b.searchState]).toEqual([[], "", "", 0, "idle"]);
        if (name === "eventClientAutocomplete") {
            expect(a.clientCases).not.toBe(b.clientCases);
            expect([a._casesGen, b._casesGen]).toEqual([1, 0]);
        }
    });
});

describe.each(NAMES)("%s getters stay reactive accessors", (name) => {
    it("keeps every getter an accessor on the instance", () => {
        const c = factories[name]();
        for (const key of GETTERS) {
            const descriptor = Object.getOwnPropertyDescriptor(c, key);
            expect(typeof descriptor?.get, key).toBe("function");
        }
    });
    it("re-evaluates getters under Alpine when state changes", async () => {
        document.body.innerHTML = `<div x-data="${name}" data-autocomplete-url="/search/"></div>`;
        Alpine.initTree(document.body);
        const data = Alpine.$data(document.body.firstElementChild);
        const seen = [];
        Alpine.effect(() => seen.push(`${data.ariaExpanded}/${data.unconfirmedQuery}/${data.activeDescendant}`));
        data.query = "abc";
        data.results = people;
        data.show = true;
        data.highlightIndex = 1;
        await Promise.resolve();
        expect(seen[0]).toBe("false/false/null");
        expect(seen.at(-1)).toBe(`true/true/${data.listboxId}-option-1`);
    });
});
