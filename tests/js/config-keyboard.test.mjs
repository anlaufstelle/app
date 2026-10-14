import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const orderButtons = `<div data-order-controls><button type="button" data-board-order="up">Up</button><button type="button" data-board-order="down">Down</button></div>`;
function field(id) {
    return `<div data-dtf-id="${id}" data-item-name="${id}" data-position-message="Field {name}: Position {position} of {total}">${orderButtons}<select data-move-target><option value="">None</option><option value="g1">One</option><option value="g2">Two</option></select><button type="button" data-board-move>Move</button></div>`;
}
function markup() {
    return `<div id="field-board-status" role="status" aria-live="polite"></div><div id="field-board" data-error-message="Change failed. Try again."><div data-sortable="blocks" data-reorder-url="/blocks" data-move-url="/move"><div data-block data-group-id="g1" data-item-name="One" data-position-message="Group {name}: Position {position} of {total}">${orderButtons}<ul data-sortable="fields" data-group-id="g1" data-reorder-url="/fields" data-move-url="/move">${field("f1")}${field("f2")}</ul></div>${field("f3").replace('<div ', '<div data-block ')}<div data-block data-group-id="g2" data-item-name="Two">${orderButtons}<ul data-sortable="fields" data-group-id="g2" data-reorder-url="/fields" data-move-url="/move"></ul></div></div></div>`;
}
const row = (id) => document.querySelector(`[data-dtf-id="${id}"]`);
const click = (element) => element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
const settle = () => document.dispatchEvent(new CustomEvent("htmx:afterSettle", { detail: { elt: document.getElementById("field-board") } }));
const status = () => document.getElementById("field-board-status").textContent;

beforeAll(async () => { await import("../../src/static/js/config-sortable.js"); });
beforeEach(() => {
    document.body.innerHTML = markup();
    delete window.Sortable;
    window.htmx = { ajax: vi.fn() };
});
afterEach(() => { settle(); vi.restoreAllMocks(); });

describe("field board keyboard actions without Sortable", () => {
    it("posts one mixed block sequence and restores focus to the opposite button at a boundary", () => {
        const group = document.querySelector('[data-block][data-group-id="g1"]');
        click(group.querySelector('[data-board-order="down"]'));
        expect(window.htmx.ajax).toHaveBeenCalledExactlyOnceWith("POST", "/blocks", expect.objectContaining({ values: { blocks: ["field:f3", "group:g1", "group:g2"] } }));
        expect(status()).toBe("");
        // Simulate the server's outerHTML replacement and boundary state.
        document.getElementById("field-board").outerHTML = document.getElementById("field-board").outerHTML;
        const replacement = document.querySelector('[data-block][data-group-id="g1"]');
        replacement.parentElement.append(replacement);
        replacement.querySelector('[data-board-order="down"]').disabled = true;
        settle();
        expect(document.activeElement).toBe(replacement.querySelector('[data-board-order="up"]'));
        expect(status()).toBe("Group One: Position 3 of 3");
    });

    it("posts the complete group field order, keeps the DOM unchanged until success, and announces template text", () => {
        click(row("f1").querySelector('[data-board-order="down"]'));
        expect(window.htmx.ajax).toHaveBeenCalledExactlyOnceWith("POST", "/fields", expect.objectContaining({ values: { group: "g1", order: ["f2", "f1"] } }));
        expect([...row("f1").parentElement.children].map((node) => node.dataset.dtfId)).toEqual(["f1", "f2"]);
        row("f1").parentElement.append(row("f1"));
        settle();
        expect(document.activeElement).toBe(row("f1").querySelector('[data-board-order="down"]'));
        expect(status()).toBe("Field f1: Position 2 of 2");
    });

    it("moves a free field into a group with one POST and restores its confirmation button", () => {
        row("f3").querySelector("select").value = "g1";
        click(row("f3").querySelector("[data-board-move]"));
        expect(window.htmx.ajax).toHaveBeenCalledExactlyOnceWith("POST", "/move", expect.objectContaining({ values: { moved: "f3", group: "g1", order: ["f1", "f2", "f3"] } }));
        document.querySelector('[data-sortable="fields"]').append(row("f3"));
        settle();
        expect(document.activeElement).toBe(row("f3").querySelector("[data-board-move]"));
        expect(status()).toBe("Field f3: Position 3 of 3");
    });

    it("moves a grouped field to the mixed top-level sequence", () => {
        click(row("f1").querySelector("[data-board-move]"));
        expect(window.htmx.ajax).toHaveBeenCalledExactlyOnceWith("POST", "/move", expect.objectContaining({ values: { moved: "f1", group: "", blocks: ["group:g1", "field:f3", "group:g2", "field:f1"] } }));
    });

    it("moves between groups with the target's complete order", () => {
        row("f1").querySelector("select").value = "g2";
        click(row("f1").querySelector("[data-board-move]"));
        expect(window.htmx.ajax).toHaveBeenCalledExactlyOnceWith("POST", "/move", expect.objectContaining({ values: { moved: "f1", group: "g2", order: ["f1"] } }));
    });

    it("does not post a same-group move or a boundary action", () => {
        row("f1").querySelector("select").value = "g1";
        click(row("f1").querySelector("[data-board-move]"));
        click(row("f1").querySelector('[data-board-order="up"]'));
        expect(window.htmx.ajax).not.toHaveBeenCalled();
    });

    it.each([400, 409, 429])("announces HTTP %s without success or optimistic mutation, and permits retry", (code) => {
        const writes = ["setItem", "removeItem", "clear"].map((method) => vi.spyOn(Storage.prototype, method));
        click(row("f1").querySelector('[data-board-order="down"]'));
        click(row("f1").querySelector('[data-board-order="down"]'));
        expect(window.htmx.ajax).toHaveBeenCalledTimes(1);
        document.dispatchEvent(new CustomEvent("htmx:responseError", { detail: { xhr: { status: code }, requestConfig: { path: "/fields" } } }));
        expect(status()).toBe("Change failed. Try again.");
        expect(row("f1").parentElement.firstElementChild).toBe(row("f1"));
        settle();
        expect(status()).toBe("Change failed. Try again.");
        writes.forEach((write) => expect(write).not.toHaveBeenCalled());
        click(row("f1").querySelector('[data-board-order="down"]'));
        expect(window.htmx.ajax).toHaveBeenCalledTimes(2);
    });
});

it("keeps item names containing position placeholders literal", () => {
    row("f1").dataset.itemName = "Name {position}";
    click(row("f1").querySelector('[data-board-order="down"]'));
    row("f1").parentElement.append(row("f1"));
    settle();
    expect(status()).toBe("Field Name {position}: Position 2 of 2");
});
