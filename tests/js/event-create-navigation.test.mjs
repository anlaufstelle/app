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

it.each(["loading", "error"])("preserves pending case intent during %s", async (state) => {
    vi.stubGlobal("fetch", state === "error"
        ? vi.fn().mockRejectedValue(new Error("Offline"))
        : vi.fn(() => new Promise(() => {})));
    const c = component();
    c._initialCaseId = "initial";
    c.loadCasesForClient("a");
    await flush();
    expect(c.casesState).toBe(state);
    const link = document.createElement("a");
    link.href = "/events/create/?template=preset&client=a&case=initial";
    c.updateContextLink({ currentTarget: link });
    expect(new URL(link.href).searchParams.get("case")).toBe("initial");
});

it.each(["loading", "error"])("blocks unresolved initial case submission during %s", async (state) => {
    vi.stubGlobal("fetch", state === "error"
        ? vi.fn().mockRejectedValue(new Error("Offline"))
        : vi.fn(() => new Promise(() => {})));
    const c = component();
    c._initialCaseId = "initial";
    c.loadCasesForClient("a");
    await flush();
    const event = { preventDefault: vi.fn() };
    c.guardCaseContext(event);
    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(c.caseContextPending).toBeTruthy();
});

it("drops pending intent on person change before the watcher runs", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    const c = component();
    c._initialCaseId = "initial";
    c.loadCasesForClient("a");
    c.selectedId = "b";
    const link = document.createElement("a");
    link.href = "/events/create/?template=preset&client=a&case=initial";
    c.updateContextLink({ currentTarget: link });
    expect(new URL(link.href).searchParams.has("case")).toBe(false);
    c.loadCasesForClient("b");
    expect(c._initialCaseId).toBe("");
});

it.each([{ cases: [{ id: "initial" }] }, { cases: [] }])("allows submit after resolving the case: %j", async ({ cases }) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok(cases)));
    const c = component();
    c._initialCaseId = "initial";
    await c.loadCasesForClient("a");
    const event = { preventDefault: vi.fn() };
    c.guardCaseContext(event);
    expect(event.preventDefault).not.toHaveBeenCalled();
    expect(c.caseContextPending).toBeFalsy();
    expect(c.selectedCaseId).toBe(cases.length ? "initial" : "");
});
