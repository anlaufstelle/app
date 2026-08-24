// #1875: Der Auswahllisten-Editor ist ein Alpine-Enhancement ueber dem echten
// options_json-Textarea. Diese Tests treiben die registrierte Komponente ohne
// Alpine-Runtime und pinnen Progressive Enhancement, Serialisierung und die
// Bestandsdaten-Warnung.
import { describe, it, expect, beforeAll, beforeEach } from "vitest";

let factory;

beforeAll(async () => {
    window.Alpine = {
        data: (name, fn) => {
            if (name === "fieldTemplateOptionsEditor") factory = fn;
        },
    };
    await import("../../src/static/js/alpine/config.js");
    document.dispatchEvent(new Event("alpine:init"));
});

function buildEditor({
    fieldType = "select",
    json = '[{"slug":"offen","label":"Offen","is_active":true}]',
    used = "",
} = {}) {
    document.body.innerHTML = `
        <form>
            <select name="field_type">
                <option value="text" ${fieldType === "text" ? "selected" : ""}>Text</option>
                <option value="select" ${fieldType === "select" ? "selected" : ""}>Auswahl</option>
                <option value="multi_select" ${fieldType === "multi_select" ? "selected" : ""}>Mehrfach</option>
            </select>
            <div id="root" x-data="fieldTemplateOptionsEditor" data-used-option-slugs="${used}">
                <div data-options-editor hidden>
                    <div data-options-list></div>
                    <button type="button" data-options-add>Option hinzufügen</button>
                    <p data-options-usage-warning hidden></p>
                </div>
                <button type="button" data-options-expert-toggle aria-expanded="false">JSON bearbeiten</button>
                <div data-options-json-wrapper><textarea name="options_json">${json}</textarea></div>
                <p data-options-json-error hidden></p>
                <template data-options-row-template>
                    <div data-option-row>
                        <label>Bezeichnung <input data-option-label></label>
                        <label>Slug <input data-option-slug></label>
                        <label><input type="checkbox" data-option-active> Aktiv</label>
                        <button type="button" data-option-remove>Entfernen</button>
                    </div>
                </template>
            </div>
        </form>`;
    return document.getElementById("root");
}

function mount(root) {
    const component = factory();
    component.$el = root;
    component.init();
    return component;
}

function click(selector) {
    document.querySelector(selector).dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

beforeEach(() => {
    document.body.innerHTML = "";
});

describe("fieldTemplateOptionsEditor", () => {
    it("liest das echte JSON-Feld ein und ersetzt es im Standardmodus durch Zeilen", () => {
        mount(buildEditor());

        expect(document.querySelector("[data-options-editor]").hidden).toBe(false);
        expect(document.querySelector("[data-options-json-wrapper]").hidden).toBe(true);
        expect(document.querySelectorAll("[data-option-row]")).toHaveLength(1);
        expect(document.querySelector("[data-option-label]").value).toBe("Offen");
        expect(document.querySelector("[data-option-slug]").value).toBe("offen");
    });

    it("fuegt Zeilen hinzu und serialisiert jede Aenderung ins Textarea", () => {
        mount(buildEditor());
        click("[data-options-add]");
        const rows = document.querySelectorAll("[data-option-row]");
        rows[1].querySelector("[data-option-label]").value = "Geschlossen";
        rows[1].querySelector("[data-option-label]").dispatchEvent(new Event("input", { bubbles: true }));
        rows[1].querySelector("[data-option-slug]").value = "geschlossen";
        rows[1].querySelector("[data-option-slug]").dispatchEvent(new Event("input", { bubbles: true }));

        expect(JSON.parse(document.querySelector("textarea").value)).toEqual([
            { slug: "offen", label: "Offen", is_active: true },
            { slug: "geschlossen", label: "Geschlossen", is_active: true },
        ]);
    });

    it("warnt nach Entfernen oder Umbenennen eines verwendeten Slugs", () => {
        mount(buildEditor({ used: "offen" }));
        click("[data-option-remove]");

        const warning = document.querySelector("[data-options-usage-warning]");
        expect(warning.hidden).toBe(false);
        expect(warning.textContent).toContain("offen");
    });

    it("laesst den Expertenmodus das echte Textarea bearbeiten und wieder einlesen", () => {
        mount(buildEditor());
        click("[data-options-expert-toggle]");
        expect(document.querySelector("[data-options-json-wrapper]").hidden).toBe(false);

        document.querySelector("textarea").value = '[{"slug":"neu","label":"Neu","is_active":false}]';
        click("[data-options-expert-toggle]");

        expect(document.querySelector("[data-options-json-wrapper]").hidden).toBe(true);
        expect(document.querySelector("[data-option-slug]").value).toBe("neu");
        expect(document.querySelector("[data-option-active]").checked).toBe(false);
    });

    it("laesst bei kaputtem Bestands-JSON den echten Fallback sichtbar", () => {
        mount(buildEditor({ json: "{kaputt" }));

        expect(document.querySelector("[data-options-editor]").hidden).toBe(true);
        expect(document.querySelector("[data-options-json-wrapper]").hidden).toBe(false);
    });

    it("aktiviert den Editor nur fuer Auswahl-Feldtypen", () => {
        mount(buildEditor({ fieldType: "text" }));
        expect(document.querySelector("[data-options-editor]").hidden).toBe(true);
        expect(document.querySelector("[data-options-json-wrapper]").hidden).toBe(false);

        const fieldType = document.querySelector("select[name='field_type']");
        fieldType.value = "multi_select";
        fieldType.dispatchEvent(new Event("change"));
        expect(document.querySelector("[data-options-editor]").hidden).toBe(false);
        expect(document.querySelector("[data-options-json-wrapper]").hidden).toBe(true);
    });
});
