import { beforeAll, beforeEach, expect, it } from "vitest";

let factory;
beforeAll(async () => {
    window.Alpine = { data: (name, fn) => {
        if (name === "documentTypeEntityFields") factory = fn;
    } };
    await import("../../src/static/js/alpine/config.js");
    document.dispatchEvent(new Event("alpine:init"));
});

function mount({ systemType = "", stage = "", disabled = false } = {}) {
    document.body.innerHTML = `<div id="root" data-min-contact-stage-defaults="ban:identified">
        <select name="entity_type"><option value="event" selected>event</option></select>
        <select name="system_type" id="id_system_type" ${disabled ? "disabled" : ""}>
            <option value="">none</option><option value="ban">ban</option><option value="crisis">crisis</option>
        </select>
        <label for="id_min_contact_stage">Stage</label>
        <select name="min_contact_stage" id="id_min_contact_stage">
            <option value="">empty</option><option value="identified">identified</option>
            <option value="qualified">qualified</option>
        </select>
    </div>`;
    const root = document.getElementById("root");
    root.dataset.entityScopeValue = "event";
    root.dataset.entityScopedFields = "";
    root.querySelector("[name=system_type]").value = systemType;
    root.querySelector("[name=min_contact_stage]").value = stage;
    const component = factory();
    component.$el = root;
    component.init();
    return root;
}

function choose(root, systemType) {
    const select = root.querySelector("[name=system_type]");
    select.value = systemType;
    select.dispatchEvent(new Event("change"));
}

const marker = (root) => root.querySelector("label[for=id_min_contact_stage] [data-required-marker]");

beforeEach(() => {
    document.body.innerHTML = "";
});

it("pre-fills the stage and marks it required as soon as the ban system type is chosen", () => {
    const root = mount();
    expect(marker(root)).toBeNull();
    choose(root, "ban");
    expect(root.querySelector("[name=min_contact_stage]").value).toBe("identified");
    expect(marker(root).hidden).toBe(false);
    expect(root.querySelector("[name=min_contact_stage]").getAttribute("aria-required")).toBe("true");
});

it("never overwrites a stage that is already chosen", () => {
    const root = mount({ stage: "qualified" });
    choose(root, "ban");
    expect(root.querySelector("[name=min_contact_stage]").value).toBe("qualified");
});

it("leaves the stage alone and drops the marker for other system types", () => {
    const root = mount();
    choose(root, "ban");
    choose(root, "crisis");
    expect(root.querySelector("[name=min_contact_stage]").value).toBe("identified");
    expect(marker(root).hidden).toBe(true);
    expect(root.querySelector("[name=min_contact_stage]").hasAttribute("aria-required")).toBe(false);
    choose(root, "");
    expect(marker(root).hidden).toBe(true);
});

it("marks the stage required on load when the system type is already a ban (edit, re-rendered form)", () => {
    const root = mount({ systemType: "ban", disabled: true });
    expect(marker(root).hidden).toBe(false);
    expect(root.querySelector("[name=min_contact_stage]").value).toBe("");
});

it("keeps working without the pre-fill markup", () => {
    document.body.innerHTML = `<div id="root"><select name="system_type"><option value="ban">ban</option></select></div>`;
    const root = document.getElementById("root");
    const component = factory();
    component.$el = root;
    expect(() => component.init()).not.toThrow();
});
