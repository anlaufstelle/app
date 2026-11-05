import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

// Blocks-sortable ``onEnd`` of the field board: where does a free field end up
// when it is dropped on a group (Refs #2688)?

function freeField(id) {
    return `<div data-block data-dtf-id="${id}" data-item-name="${id}"></div>`;
}
function groupCard(id, fields) {
    const rows = fields.map((f) => `<li data-dtf-id="${f}" data-item-name="${f}"></li>`).join("");
    return `<div data-block data-group-id="${id}" data-item-name="${id}"><ul data-sortable="fields" data-group-id="${id}" data-reorder-url="/fields" data-move-url="/move">${rows}</ul></div>`;
}
function markup() {
    return `<div id="field-board"><div data-sortable="blocks" data-reorder-url="/blocks" data-move-url="/move">${groupCard("g1", ["f1", "f2"])}${freeField("f3")}${groupCard("g2", [])}</div></div>`;
}

const row = (id) => document.querySelector(`[data-dtf-id="${id}"]`);
let blocksOptions;

beforeAll(async () => {
    await import("../../src/static/js/config-sortable.js");
});

beforeEach(() => {
    document.body.innerHTML = markup();
    blocksOptions = null;
    window.Sortable = {
        create: vi.fn((element, options) => {
            if (element.getAttribute("data-sortable") === "blocks") {
                blocksOptions = options;
            }
            return { destroy() {} };
        }),
    };
    window.htmx = { ajax: vi.fn() };
    document.dispatchEvent(new Event("DOMContentLoaded"));
});

afterEach(() => {
    vi.restoreAllMocks();
    delete document.elementFromPoint;
});

function dropEvent(item, from, to) {
    return { item, from, to, originalEvent: { clientX: 10, clientY: 20 } };
}

describe("blocks onEnd for a free field dropped on a group", () => {
    it("takes the group list SortableJS already handed the row to and does not append it twice", () => {
        const container = document.querySelector('[data-sortable="blocks"]');
        const list = document.querySelector('[data-sortable="fields"][data-group-id="g2"]');
        // SortableJS moved the row into the nested group list before ``onEnd``.
        list.append(row("f3"));
        // The point under the pointer is the dragged row itself (it moved under the pointer).
        document.elementFromPoint = vi.fn(() => row("f3"));

        blocksOptions.onEnd(dropEvent(row("f3"), container, list));

        expect(window.htmx.ajax).toHaveBeenCalledExactlyOnceWith(
            "POST",
            "/move",
            expect.objectContaining({ values: { moved: "f3", group: "g2", order: ["f3"] } })
        );
    });

    it("keeps the position of the row inside the adopted list", () => {
        const container = document.querySelector('[data-sortable="blocks"]');
        const list = document.querySelector('[data-sortable="fields"][data-group-id="g1"]');
        list.insertBefore(row("f3"), row("f2"));
        document.elementFromPoint = vi.fn(() => list);

        blocksOptions.onEnd(dropEvent(row("f3"), container, list));

        expect(window.htmx.ajax).toHaveBeenCalledExactlyOnceWith(
            "POST",
            "/move",
            expect.objectContaining({ values: { moved: "f3", group: "g1", order: ["f1", "f3", "f2"] } })
        );
    });

    it("still appends the row when it stayed in the blocks list and the pointer is over a group list", () => {
        const container = document.querySelector('[data-sortable="blocks"]');
        const list = document.querySelector('[data-sortable="fields"][data-group-id="g1"]');
        document.elementFromPoint = vi.fn(() => list);

        blocksOptions.onEnd(dropEvent(row("f3"), container, container));

        expect(window.htmx.ajax).toHaveBeenCalledExactlyOnceWith(
            "POST",
            "/move",
            expect.objectContaining({ values: { moved: "f3", group: "g1", order: ["f1", "f2", "f3"] } })
        );
    });

    it("posts a plain block reorder when the row stayed top-level and no group is under the pointer", () => {
        const container = document.querySelector('[data-sortable="blocks"]');
        document.elementFromPoint = vi.fn(() => container);

        blocksOptions.onEnd(dropEvent(row("f3"), container, container));

        expect(window.htmx.ajax).toHaveBeenCalledExactlyOnceWith(
            "POST",
            "/blocks",
            expect.objectContaining({ values: { blocks: ["group:g1", "field:f3", "group:g2"] } })
        );
    });
});
