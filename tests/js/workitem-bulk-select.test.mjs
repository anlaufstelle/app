import { readFileSync } from "node:fs";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import Alpine from "@alpinejs/csp";

const template = readFileSync("src/templates/core/workitems/inbox.html", "utf8");
const bindings = template.match(/<div\s+x-data="workitemBulkSelect"[\s\S]*?>/)[0];

beforeAll(async () => {
    window.Alpine = Alpine;
    await import("../../src/static/js/alpine/dashboards.js");
    document.dispatchEvent(new Event("alpine:init"));
});

beforeEach(() => {
    Alpine.destroyTree(document.body);
    document.body.innerHTML = `${bindings}
        <div x-show="hasSelection"><span x-text="selectionCount"></span>
        ${Array.from({ length: 3 }, () => `<form><template x-for="id in selected"><input type="hidden" name="workitem_ids" :value="id"></template></form>`).join("")}</div>
        <input id="workitem-select-all" type="checkbox">
        <div id="inbox-content"><input type="checkbox" name="workitem_ids" value="1" checked></div>
    </div>`;
    Alpine.initTree(document.body);
});

async function settle() {
    await Alpine.nextTick();
}

function expectSelection(ids, master) {
    const component = Alpine.$data(document.querySelector("[x-data]"));
    expect(component.selected).toEqual(ids);
    expect(component.selectionCount).toBe(ids.length);
    expect(component.hasSelection).toBe(ids.length > 0);
    expect(document.getElementById("workitem-select-all").checked).toBe(master);
    document.querySelectorAll("form").forEach(form => {
        expect(Array.from(form.querySelectorAll("input"), input => input.value)).toEqual(ids);
    });
}

describe("visible work item selection", () => {
    it("excludes rows inside an open inner details with a closed outer details", async () => {
        document.getElementById("inbox-content").innerHTML = '<details><details open><input type="checkbox" name="workitem_ids" value="4" checked></details></details>';
        document.dispatchEvent(new CustomEvent("htmx:after-settle", { bubbles: true }));
        await settle();
        expectSelection([], false);
    });

    it("rebuilds selection after inbox and row swaps including OOB insertion", async () => {
        await settle();
        expectSelection(["1"], true);
        document.getElementById("inbox-content").innerHTML = '<input type="checkbox" name="workitem_ids" value="2" checked>';
        document.dispatchEvent(new CustomEvent("htmx:after-settle", { bubbles: true }));
        await settle();
        expectSelection(["2"], true);
        document.querySelector("#inbox-content input").outerHTML = '<details><input type="checkbox" name="workitem_ids" value="2" checked></details>';
        document.dispatchEvent(new CustomEvent("htmx:after-settle", { bubbles: true }));
        await settle();
        expectSelection([], false);
    });

    it("drops hidden checked rows when details close and restores visible rows when opened", async () => {
        const content = document.getElementById("inbox-content");
        content.innerHTML = '<details open><input type="checkbox" name="workitem_ids" value="3" checked></details>';
        const details = content.firstChild;
        details.dispatchEvent(new Event("toggle"));
        await settle();
        expectSelection(["3"], true);
        details.open = false;
        details.dispatchEvent(new Event("toggle"));
        await settle();
        expectSelection([], false);
    });
});
