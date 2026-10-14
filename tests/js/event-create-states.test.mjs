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

it("clears old cases immediately and ignores a late previous-person response", async () => {
    const pending = [];
    vi.stubGlobal("fetch", vi.fn(() => new Promise((resolve) => pending.push(resolve))));
    const c = component();
    c.clientCases = [{ id: "old" }];
    c.selectedCaseId = "old";
    c.loadCasesForClient("a");
    expect(c.clientCases).toEqual([]);
    expect(c.selectedCaseId).toBe("");
    c.selectedId = "b";
    c.loadCasesForClient("b");
    pending[1](ok([{ id: "b-case" }]));
    await flush();
    c.selectedCaseId = "b-case";
    pending[0](ok([{ id: "a-case" }]));
    await flush();
    expect(c.clientCases).toEqual([{ id: "b-case" }]);
    expect(c.selectedCaseId).toBe("b-case");
});

it.each(["clear", "type", "old-error"])("invalidates pending cases on %s", async (action) => {
    const pending = [];
    vi.stubGlobal("fetch", vi.fn(() => new Promise((resolve, reject) => pending.push({ resolve, reject }))));
    const c = component();
    c._initialCaseId = "a-case";
    c.loadCasesForClient("a");
    if (action === "type") {
        c.updateAnonymousAllowed({ options: [{ dataset: { minStage: "identified" } }], selectedIndex: 0 });
    } else {
        c.selectedId = action === "clear" ? "" : "b";
        c.loadCasesForClient(c.selectedId);
    }
    if (action === "old-error") {
        pending[1].resolve(ok([{ id: "b-case" }]));
        await flush();
        pending[0].reject(new Error("Offline"));
        await flush();
        expect(c.casesState).toBe("success");
        expect(c.clientCases).toEqual([{ id: "b-case" }]);
    } else {
        pending[0].resolve(ok([{ id: "a-case" }]));
        await flush();
        expect(c.clientCases).toEqual([]);
        expect(c.selectedCaseId).toBe("");
    }
});

it.each(["http", "network", "malformed"])("distinguishes case loading, %s errors, retry and successful empty", async (failure) => {
    const fetchMock = vi.fn();
    if (failure === "http") fetchMock.mockResolvedValueOnce({ ok: false, status: 500 });
    if (failure === "network") fetchMock.mockRejectedValueOnce(new Error("Offline"));
    if (failure === "malformed") fetchMock.mockResolvedValueOnce(ok({ error: true }));
    fetchMock.mockResolvedValueOnce(ok([]));
    vi.stubGlobal("fetch", fetchMock);
    const c = component();
    c.loadCasesForClient("a");
    expect(c.casesState).toBe("loading");
    expect(c.showNoCasesHint).toBeFalsy();
    await flush();
    expect(c.casesState).toBe("error");
    expect(c.showNoCasesHint).toBeFalsy();
    c.retryCases();
    await flush();
    expect(c.casesState).toBe("success");
    expect(c.showNoCasesHint).toBeTruthy();
});

it("retains the server initial case through retry but discards it on person change", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValueOnce(new Error("Offline")).mockResolvedValue(ok([{ id: "initial" }])));
    const c = component();
    c._initialCaseId = "initial";
    c.loadCasesForClient("a");
    await flush();
    c.retryCases();
    await flush();
    expect(c.selectedCaseId).toBe("initial");
    c._initialCaseId = "initial";
    c.selectedId = "b";
    c.loadCasesForClient("b");
    await flush();
    expect(c.selectedCaseId).toBe("");
});


describe.each(["clientAutocomplete", "eventClientAutocomplete"])("%s search", (name) => {
    it.each([429, 500, "network", "html"])("invalidates old results on %s and recovers", async (failure) => {
        const fetchMock = vi.fn();
        if (failure === "network") fetchMock.mockRejectedValueOnce(new Error("Offline"));
        else if (failure === "html") fetchMock.mockResolvedValueOnce({ ok: true, json: async () => { throw new SyntaxError("HTML"); } });
        else fetchMock.mockResolvedValueOnce({ ok: false, status: failure, json: async () => [{ id: "stale" }] });
        fetchMock.mockResolvedValueOnce(ok([]));
        vi.stubGlobal("fetch", fetchMock);
        const c = component(name);
        c.query = "query";
        c.results = [{ id: "old" }];
        c.enrichedResults = [{ id: "old" }];
        c.highlightIndex = 0;
        c.fetchResults();
        expect(c.results).toEqual([]);
        expect(c.enrichedResults).toEqual([]);
        expect(c.searchState).toBe("loading");
        await flush();
        expect(c.searchState).toBe("error");
        expect(c.noMatches).toBeFalsy();
        c.confirmHighlight();
        expect(c.selectedId).toBe("a");
        c.fetchResults();
        await flush();
        expect(c.searchState).toBe("success");
        expect(c.noMatches).toBeTruthy();
    });
});
