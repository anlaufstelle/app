import { beforeAll, expect, it } from "vitest";

let factory;
beforeAll(async () => {
    window.Alpine = { data: (name, fn) => {
        if (name === "documentTypeEntityFields") factory = fn;
    } };
    await import("../../src/static/js/alpine/config.js");
    document.dispatchEvent(new Event("alpine:init"));
});

it("shows report names and warnings separately and clears stale warnings", () => {
    document.body.innerHTML = `<div id="root">
        <select name="report_key"><option value="needle_exchange">Counter</option>
        <option value="">Empty</option><option value="excluded">Excluded</option></select>
        <p data-report-key-bindings-output></p>
        <div data-report-key-warnings-container hidden><p data-report-key-warnings-output></p></div>
    </div>`;
    const root = document.getElementById("root");
    root.dataset.reportKeyBindings = JSON.stringify({ needle_exchange: ["Balance"] });
    root.dataset.reportKeyWarnings = JSON.stringify({ needle_exchange: ["Missing return"] });
    root.dataset.reportKeyExcludedValue = "excluded";
    const component = factory();
    component.$el = root;
    component.init();
    expect(root.querySelector("[data-report-key-bindings-output]").textContent).toBe("Balance");
    expect(root.querySelector("[data-report-key-warnings-output]").textContent).toBe("Missing return");
    expect(root.querySelector("[data-report-key-warnings-container]").hidden).toBe(false);
    for (const key of ["", "excluded"]) {
        component.reportKeySelect.value = key;
        component.reportKeySelect.dispatchEvent(new Event("change"));
        expect(root.querySelector("[data-report-key-warnings-output]").textContent).toBe("");
        expect(root.querySelector("[data-report-key-warnings-container]").hidden).toBe(true);
    }
});
