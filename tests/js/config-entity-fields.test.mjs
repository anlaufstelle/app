// Refs #1757 (Option C aus #1731): Die Alpine-Komponente
// ``documentTypeEntityFields`` blendet im Dokumentationstyp-Formular die nur
// fuer Ereignis-Typen wirksamen Felder aus, sobald ein anderer Entitaetstyp
// gewaehlt ist.
//
// Zwei Eigenschaften sind hier sicherheits-/datenrelevant und deshalb
// ausdruecklich getestet:
//
//   1. Ausgeblendet wird NUR visuell — die Eingabefelder bleiben im DOM und
//      aktiviert, ihr Wert wird also unveraendert mitgepostet. Kein Feld darf
//      durchs Ausblenden auf den Modell-Default zurueckfallen.
//   2. Die Feldliste kommt als data-Attribut vom Server
//      (``DocumentTypeForm.EVENT_ONLY_FIELDS``); die Komponente fuehrt KEINEN
//      eigenen Katalog. Der Test setzt deshalb bewusst eine abweichende Liste.
//
// config.js registriert die Komponente ueber ``Alpine.data(name, factory)`` im
// ``alpine:init``-Event — wir fangen die Factory mit einem Alpine-Stub ab und
// treiben sie direkt (Muster tests/js/conflict-list-export.test.mjs).
import { describe, it, expect, beforeAll, beforeEach } from "vitest";

let factory;

beforeAll(async () => {
    window.Alpine = {
        data: (name, fn) => {
            if (name === "documentTypeEntityFields") factory = fn;
        },
    };
    await import("../../src/static/js/alpine/config.js");
    document.dispatchEvent(new Event("alpine:init"));
});

const SCOPED = "system_type min_contact_stage retention_days";

function buildForm({ scopedFields = SCOPED, withSelect = true, errorOn = null } = {}) {
    const fields = ["system_type", "min_contact_stage", "retention_days"];
    const select = withSelect
        ? `<div>
               <select id="id_entity_type" name="entity_type">
                   <option value="event" selected>Ereignis</option>
                   <option value="client">Person</option>
                   <option value="case">Fall</option>
               </select>
           </div>`
        : "";
    const scoped = fields
        .map(
            (name) => `<div data-testid="wrap-${name}">
                   <label for="id_${name}">${name}</label>
                   <input id="id_${name}" name="${name}">
                   ${errorOn === name ? '<ul role="alert"><li>Fehler</li></ul>' : ""}
               </div>`,
        )
        .join("");
    document.body.innerHTML = `
        <div id="root"
             data-entity-scope-value="event"
             data-entity-scoped-fields="${scopedFields}">
            <div><label for="id_name">Name</label><input id="id_name" name="name"></div>
            ${select}
            ${scoped}
        </div>`;
    return document.getElementById("root");
}

function mount(root) {
    const component = factory();
    component.$el = root;
    component.init();
    return component;
}

function wrapper(name) {
    return document.querySelector(`[data-testid="wrap-${name}"]`);
}

function selectEntityType(value) {
    const select = document.getElementById("id_entity_type");
    select.value = value;
    select.dispatchEvent(new Event("change"));
}

beforeEach(() => {
    document.body.innerHTML = "";
});

describe("documentTypeEntityFields", () => {
    it("laesst bei entity_type=event alle Felder sichtbar", () => {
        mount(buildForm());
        expect(wrapper("system_type").hidden).toBe(false);
        expect(wrapper("min_contact_stage").hidden).toBe(false);
        expect(wrapper("retention_days").hidden).toBe(false);
    });

    it("blendet die ereignis-only-Felder bei Person/Fall aus", () => {
        mount(buildForm());
        selectEntityType("client");
        expect(wrapper("system_type").hidden).toBe(true);
        expect(wrapper("min_contact_stage").hidden).toBe(true);
        expect(wrapper("retention_days").hidden).toBe(true);

        selectEntityType("case");
        expect(wrapper("retention_days").hidden).toBe(true);
    });

    it("blendet die Felder beim Rueckwechsel auf Ereignis wieder ein", () => {
        mount(buildForm());
        selectEntityType("client");
        selectEntityType("event");
        expect(wrapper("system_type").hidden).toBe(false);
        expect(wrapper("min_contact_stage").hidden).toBe(false);
        expect(wrapper("retention_days").hidden).toBe(false);
    });

    it("laesst nicht betroffene Felder immer sichtbar", () => {
        const root = mount(buildForm()).$el;
        selectEntityType("client");
        // ``name`` ist Teil des Natural Keys (facility, name) und darf nie
        // verschwinden.
        expect(root.querySelector("#id_name").closest("div").hidden).toBe(false);
        expect(root.querySelector("#id_entity_type").closest("div").hidden).toBe(false);
    });

    it("blendet nur aus, ohne die Eingabefelder zu entfernen oder zu deaktivieren", () => {
        mount(buildForm());
        selectEntityType("client");
        // Die Werte muessen weiter im POST landen — sonst fiele das Feld auf
        // den Modell-Default zurueck.
        ["system_type", "min_contact_stage", "retention_days"].forEach((name) => {
            const input = document.getElementById(`id_${name}`);
            expect(input).not.toBeNull();
            expect(input.disabled).toBe(false);
            expect(input.type).not.toBe("hidden");
            expect(input.getAttribute("name")).toBe(name);
        });
    });

    it("haelt ein Feld mit Fehlermeldung sichtbar", () => {
        mount(buildForm({ errorOn: "retention_days" }));
        selectEntityType("client");
        // Eine unsichtbare Validierungsmeldung blockierte das Formular sonst
        // ohne erkennbaren Grund.
        expect(wrapper("retention_days").hidden).toBe(false);
        expect(wrapper("system_type").hidden).toBe(true);
    });

    it("fuehrt keinen eigenen Feldkatalog — die Liste kommt aus dem data-Attribut", () => {
        mount(buildForm({ scopedFields: "retention_days" }));
        selectEntityType("client");
        expect(wrapper("retention_days").hidden).toBe(true);
        expect(wrapper("system_type").hidden).toBe(false);
        expect(wrapper("min_contact_stage").hidden).toBe(false);
    });

    it("blendet ohne Entitaetstyp-Select nichts aus", () => {
        expect(() => mount(buildForm({ withSelect: false }))).not.toThrow();
        expect(wrapper("system_type").hidden).toBe(false);
    });
});
