import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

beforeAll(async () => {
    await import("../../src/static/js/zeitstrom-filter-state.js");
});

beforeEach(() => {
    document.body.innerHTML = `
        <form id="zeitstrom-filter-state">
            <input name="date" value="2026-06-22">
            <input name="time_filter" value="all">
            <input name="type" value="events">
            <input name="doc_type" value="old-type">
            <input name="view" value="">
        </form>
        <button class="time-filter-tab" data-zeitstrom-time-filter="night"></button>
        <select id="filter-doc-type"><option value="new-type" selected>Neu</option></select>`;
});

function configure(source) {
    const parameters = { time_filter: "stale", doc_type: "stale" };
    source.dispatchEvent(
        new CustomEvent("htmx:configRequest", { bubbles: true, detail: { elt: source, parameters } }),
    );
    return parameters;
}

describe("Zeitstrom filter state", () => {
    it("writes a clicked time filter before its HTMX request", () => {
        const parameters = configure(document.querySelector(".time-filter-tab"));

        expect(document.querySelector('[name="time_filter"]').value).toBe("night");
        expect(parameters.time_filter).toBe("night");
    });

    it("writes the changed document-type filter before its HTMX request", () => {
        const parameters = configure(document.getElementById("filter-doc-type"));

        expect(document.querySelector('[name="doc_type"]').value).toBe("new-type");
        expect(parameters.doc_type).toBe("new-type");
    });

    it("announces the hidden SSOT change so the live watcher invalidates its generation", () => {
        const changed = vi.fn();
        window.addEventListener("zeitstrom:filter-changed", changed, { once: true });

        configure(document.getElementById("filter-doc-type"));

        expect(changed).toHaveBeenCalledOnce();
        expect(changed.mock.calls[0][0].detail).toEqual({ name: "doc_type", value: "new-type" });
    });

    it("leaves unrelated requests and pages without the state source unchanged", () => {
        const unrelated = document.createElement("button");
        const parameters = configure(unrelated);
        expect(document.querySelector('[name="time_filter"]').value).toBe("all");
        expect(document.querySelector('[name="doc_type"]').value).toBe("old-type");
        expect(parameters.time_filter).toBe("stale");
        expect(parameters.doc_type).toBe("stale");

        document.getElementById("zeitstrom-filter-state").remove();
        expect(() => configure(document.querySelector(".time-filter-tab"))).not.toThrow();
    });
});
