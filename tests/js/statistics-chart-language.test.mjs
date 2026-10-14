import { beforeAll, beforeEach, expect, it, vi } from "vitest";

let factory;
beforeAll(async () => {
    window.Alpine = { data: (_name, fn) => { factory = fn; } };
    await import("../../src/static/js/statistics-charts.js");
    document.dispatchEvent(new Event("alpine:init"));
});
beforeEach(() => {
    document.body.innerHTML = '<div id="statistics-charts"><canvas id="chart-contacts"></canvas><canvas id="chart-doc-types"></canvas></div>';
    window.Chart = vi.fn(function (_canvas, options) { this.options = options; });
    window.Chart.getChart = vi.fn();
});

it.each([
    ["de", ["Gesamt", "Anonym", "Nur Pseudonym", "Mit Zusatzangaben"], "Kontakte", "Quelle: ", "Live-Daten", "Fehler: ", "HTTP-Fehler"],
    ["en", ["Total", "Anonymous", "Pseudonym only", "With additional details"], "Contacts", "Source: ", "Live data", "Error: ", "HTTP error"],
])("uses %s attributes for legends, sources and HTTP errors", async (_language, labels, contacts, source, live, error, http) => {
    const host = document.getElementById("statistics-charts");
    Object.assign(host.dataset, {
        i18nTotal: labels[0], i18nAnonymous: labels[1], i18nPseudonym: labels[2], i18nQualified: labels[3],
        i18nContacts: contacts, i18nSource: source, i18nSnapshot: "Snapshot", i18nLive: live, i18nError: error, i18nHttp: http,
    });
    const component = factory();
    component.$el = host;
    const data = { labels: ["2026-10"], sources: ["snapshot", "live"], contacts: { total: [1], anonym: [1], identifiziert: [0], qualifiziert: [0] } };
    component.renderContactsChart(data);
    const config = component.charts.contacts.options;
    expect(config.data.datasets.map(d => d.label)).toEqual(labels);
    const tooltip = config.options.plugins.tooltip.callbacks.afterLabel;
    expect(tooltip({ dataIndex: 0 })).toBe(source + "Snapshot");
    expect(tooltip({ dataIndex: 1 })).toBe(source + live);
    expect(data.sources).toEqual(["snapshot", "live"]);
    component.renderDocTypeChart({ document_types: [] });
    expect(component.charts.docTypes.options.data.datasets[0].label).toBe(contacts);
    window.fetch = vi.fn(async () => ({ ok: false, status: 403 }));
    await component.fetchAndRender();
    expect(component.errorText).toBe(error + http + " 403");
});

it("uses translated network failure text rather than the browser exception", async () => {
    const component = factory();
    component.$el = document.getElementById("statistics-charts");
    component.$el.dataset.i18nRequestFailed = "Translated request failure";
    const error = new Error("Browser-language exception");
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    window.fetch = vi.fn(async () => { throw error; });
    await component.fetchAndRender();
    expect(component.error).toBe("Translated request failure");
    expect(log).toHaveBeenCalledWith(error);
    log.mockRestore();
});
