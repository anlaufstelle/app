import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

let createComponent;
let script;
let chartInstances;
let ChartConstructor;
let consoleError;

const data = {
    labels: ["2026-11-01"],
    sources: ["live"],
    contacts: { total: [8], anonym: [2], identifiziert: [3], qualifiziert: [3] },
    document_types: [{ name: "Contact", count: 8 }],
    age_clusters: [{ label: "18–30", count: 8 }],
};

function mountCharts(query = "zeitraum=jahr") {
    document.body.innerHTML = `
        <div id="statistics-charts" data-chart-query="${query}">
            <canvas id="chart-contacts"></canvas>
            <canvas id="chart-doc-types"></canvas>
            <canvas id="chart-age-clusters"></canvas>
        </div>`;
    const component = createComponent();
    component.$el = document.getElementById("statistics-charts");
    return component;
}

function loadLibrary() {
    vi.stubGlobal("Chart", ChartConstructor);
    script.dispatchEvent(new Event("load"));
}

beforeAll(async () => {
    vi.stubGlobal("Alpine", {
        data(name, factory) {
            if (name === "statisticsCharts") createComponent = factory;
        },
    });
    await import("../../src/static/js/statistics-charts.js");
    document.dispatchEvent(new Event("alpine:init"));
});

beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(document, "readyState", "get").mockReturnValue("interactive");
    vi.stubGlobal("Chart", undefined);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => data }));
    consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    chartInstances = new Map();
    ChartConstructor = vi.fn(function (canvas) {
        const chart = { canvas, destroy: vi.fn(() => chartInstances.delete(canvas)) };
        chartInstances.set(canvas, chart);
        return chart;
    });
    ChartConstructor.getChart = (canvas) => chartInstances.get(canvas);
    script = document.createElement("script");
    script.src = "/static/js/chart.min.aa284506853a.js";
    script.dataset.chartLibrary = "";
    script.defer = true;
    document.head.appendChild(script);
    document.body.removeAttribute("data-charts-ready");
});

afterEach(() => {
    script.remove();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
});

describe.each(["/static/js/chart.min.js", "/static/js/chart.min.aa284506853a.js"])("statistics chart library loading (%s)", (libraryPath) => {
    beforeEach(() => {
        script.src = libraryPath;
    });
    it.each(["interactive", "complete"])("renders once when the library is already available (%s)", async (readyState) => {
        vi.spyOn(document, "readyState", "get").mockReturnValue(readyState);
        loadLibrary();
        const component = mountCharts();
        await component.fetchAndRender();
        expect(ChartConstructor).toHaveBeenCalledTimes(3);
        expect(fetch).toHaveBeenCalledTimes(1);
        expect(component.error).toBeNull();
        expect(component.loading).toBe(false);
        expect(consoleError).not.toHaveBeenCalled();
    });

    it("keeps the data while waiting and renders exactly once on library load", async () => {
        const component = mountCharts();
        const rendering = component.fetchAndRender();
        await vi.advanceTimersByTimeAsync(100);
        expect(component.loading).toBe(true);
        expect(component.error).toBeNull();
        expect(ChartConstructor).not.toHaveBeenCalled();
        expect(document.body.hasAttribute("data-charts-ready")).toBe(false);
        loadLibrary();
        await rendering;
        script.dispatchEvent(new Event("load"));
        expect(ChartConstructor).toHaveBeenCalledTimes(3);
        expect(fetch).toHaveBeenCalledTimes(1);
        expect(component.loading).toBe(false);
        expect(component.error).toBeNull();
        expect(document.body.dataset.chartsReady).toBe("true");
        expect(consoleError).not.toHaveBeenCalled();
    });

    it.each(["error", "load"])("ends waiting when the script emits %s without exposing Chart", async (event) => {
        const component = mountCharts();
        const rendering = component.fetchAndRender();
        await vi.advanceTimersByTimeAsync(100);
        expect(component.loading).toBe(true);
        expect(component.error).toBeNull();
        script.dispatchEvent(new Event(event));
        await rendering;
        expect(component.error).toBe("Request failed.");
        expect(component.loading).toBe(false);
        expect(ChartConstructor).not.toHaveBeenCalled();
        expect(fetch).toHaveBeenCalledTimes(1);
        expect(vi.getTimerCount()).toBe(0);
    });

    it("ends waiting if the script never completes", async () => {
        const component = mountCharts();
        const rendering = component.fetchAndRender();
        await vi.advanceTimersByTimeAsync(100);
        expect(component.loading).toBe(true);
        await vi.advanceTimersByTimeAsync(30000);
        await rendering;
        expect(component.error).toBe("Request failed.");
        expect(component.loading).toBe(false);
        expect(fetch).toHaveBeenCalledTimes(1);
    });

    it("reports a missing library script without waiting", async () => {
        script.remove();
        const component = mountCharts();
        await component.fetchAndRender();
        expect(component.error).toBe("Request failed.");
        expect(component.loading).toBe(false);
        expect(ChartConstructor).not.toHaveBeenCalled();
        expect(fetch).toHaveBeenCalledTimes(1);
        expect(vi.getTimerCount()).toBe(0);
    });

    it("reports a library that failed before the data arrived", async () => {
        let respond;
        fetch.mockImplementation(() => new Promise((resolve) => { respond = resolve; }));
        const component = mountCharts();
        const rendering = component.fetchAndRender();
        script.dispatchEvent(new Event("error"));
        vi.spyOn(document, "readyState", "get").mockReturnValue("complete");
        respond({ ok: true, json: async () => data });
        await rendering;
        expect(component.error).toBe("Request failed.");
        expect(component.loading).toBe(false);
        expect(vi.getTimerCount()).toBe(0);
    });

    it("settles a missed script error at window load", async () => {
        script.dispatchEvent(new Event("error"));
        const component = mountCharts();
        const rendering = component.fetchAndRender();
        await vi.advanceTimersByTimeAsync(100);
        expect(component.loading).toBe(true);
        window.dispatchEvent(new Event("load"));
        await rendering;
        expect(component.error).toBe("Request failed.");
        expect(component.loading).toBe(false);
        expect(vi.getTimerCount()).toBe(0);
    });

    it("destroys the old charts before a filter swap and renders the new query once", async () => {
        loadLibrary();
        const original = mountCharts();
        await original.fetchAndRender();
        const previousCharts = [...chartInstances.values()];
        document.dispatchEvent(new CustomEvent("htmx:beforeSwap", { detail: { target: original.$el } }));
        for (const chart of previousCharts) expect(chart.destroy).toHaveBeenCalledTimes(1);
        const filtered = mountCharts("zeitraum=90");
        await filtered.fetchAndRender();
        expect(ChartConstructor).toHaveBeenCalledTimes(6);
        expect(chartInstances.size).toBe(3);
        expect(fetch).toHaveBeenCalledTimes(2);
        expect(fetch.mock.calls[1][0]).toBe("/statistics/chart-data/?zeitraum=90");
        expect(consoleError).not.toHaveBeenCalled();
    });

    it("does not render a swapped-out component when the library arrives", async () => {
        const original = mountCharts();
        const firstRendering = original.fetchAndRender();
        await vi.advanceTimersByTimeAsync(100);
        document.dispatchEvent(new CustomEvent("htmx:beforeSwap", { detail: { target: original.$el } }));
        const replacement = mountCharts("zeitraum=90");
        const secondRendering = replacement.fetchAndRender();
        await vi.advanceTimersByTimeAsync(100);
        loadLibrary();
        await Promise.all([firstRendering, secondRendering]);
        expect(ChartConstructor).toHaveBeenCalledTimes(3);
        expect(chartInstances.size).toBe(3);
        expect(fetch).toHaveBeenCalledTimes(2);
        expect(original.error).toBeNull();
        expect(replacement.error).toBeNull();
        expect(consoleError).not.toHaveBeenCalled();
    });
});
