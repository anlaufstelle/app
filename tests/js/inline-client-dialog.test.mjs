import { readFileSync } from "node:fs";
import Alpine from "@alpinejs/csp";
import { beforeAll, afterEach, expect, it, vi } from "vitest";

const factories = {};
const controllers = [];
beforeAll(async () => {
    window.Alpine = Alpine;
    const register = Alpine.data.bind(Alpine);
    vi.spyOn(Alpine, "data").mockImplementation((name, factory) => {
        factories[name] = factory;
        register(name, factory);
    });
    await import("../../src/static/js/alpine/forms.js");
    document.dispatchEvent(new Event("alpine:init"));
});
afterEach(() => {
    for (const controller of controllers.splice(0)) controller.destroy();
    Alpine.destroyTree(document.body);
    document.body.innerHTML = "";
});
function setup() {
    document.body.innerHTML = '<nav inert><button>Already inert</button></nav><main>' +
        '<form id="event-create-form"><input role="combobox" value="Unchanged text">' +
        '<textarea>Outer draft</textarea><button type="button" id="opener">Create</button></form>' +
        '<div id="client-inline-create-modal"></div></main><footer><button>Background</button></footer>';
    const slot = document.getElementById("client-inline-create-modal");
    const opener = document.getElementById("opener");
    const input = document.querySelector('input[role="combobox"]');
    opener.focus();
    const c = factories.inlineClientDialog();
    c.$el = slot;
    c.$nextTick = (callback) => callback();
    c.init();
    controllers.push(c);
    const event = { detail: { target: slot } };
    function swap(markup) {
        c.beforeSwap(event);
        slot.innerHTML = markup;
        c.afterSwap(event);
    }
    function open(invalid = false) {
        swap('<div role="dialog" tabindex="-1"><input id="first"' + (invalid ? '' : ' autofocus') + '>' +
            '<select id="error"' + (invalid ? ' aria-invalid="true"' : '') + '><option>Stage</option></select>' +
            '<button id="last" data-inline-client-cancel>Cancel</button></div>');
    }
    return { c, slot, opener, input, swap, open };
}
const key = (value, options = {}) => new KeyboardEvent("keydown", { key: value, bubbles: true, cancelable: true, ...options });

it("makes only the background inert and restores only owned inert attributes", () => {
    const { c, open, swap, slot } = setup();
    open();
    expect(document.getElementById("event-create-form").hasAttribute("inert")).toBe(true);
    expect(document.querySelector("footer").hasAttribute("inert")).toBe(true);
    expect(slot.closest("[inert]")).toBeNull();
    expect(document.activeElement.id).toBe("first");
    swap("");
    expect(document.getElementById("event-create-form").hasAttribute("inert")).toBe(false);
    expect(document.querySelector("footer").hasAttribute("inert")).toBe(false);
    expect(document.querySelector("nav").hasAttribute("inert")).toBe(true);
    c.destroy();
});
it("wraps Tab and Shift+Tab inside the dialog", () => {
    const { c, open } = setup();
    open();
    document.getElementById("last").focus();
    const tab = key("Tab");
    c.onKeydown(tab);
    expect(document.activeElement.id).toBe("first");
    expect(tab.defaultPrevented).toBe(true);
    const back = key("Tab", { shiftKey: true });
    c.onKeydown(back);
    expect(document.activeElement.id).toBe("last");
    expect(back.defaultPrevented).toBe(true);
});
it("closes with plain Escape through Cancel and leaves modified Escape untouched", () => {
    const { c, open, swap, opener } = setup();
    open();
    const cancel = document.querySelector("[data-inline-client-cancel]");
    const click = vi.fn(() => swap(""));
    cancel.addEventListener("click", click);
    for (const modifier of ["shiftKey", "ctrlKey", "altKey", "metaKey"]) {
        const event = key("Escape", { [modifier]: true });
        c.onKeydown(event);
        expect(event.defaultPrevented).toBe(false);
        expect(click).not.toHaveBeenCalled();
    }
    const escape = key("Escape");
    c.onKeydown(escape);
    expect(click).toHaveBeenCalledOnce();
    expect(escape.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(opener);
});
it.each(["hidden", "removed"])("returns focus to the person input when the opener is %s", (state) => {
    const { open, swap, opener, input, slot } = setup();
    open();
    if (state === "hidden") opener.hidden = true;
    else opener.remove();
    swap("");
    expect(document.activeElement).toBe(input);
    expect(document.activeElement).not.toBe(slot);
});
it("returns focus to the opener on Cancel and to the person input on success", () => {
    const { c, open, swap, opener, input, slot } = setup();
    open();
    swap("");
    expect(document.activeElement).toBe(opener);
    open();
    const xhr = { getResponseHeader: () => '{"client-inline-created":{"id":"new"}}' };
    c.beforeRequest({ detail: { target: slot, xhr, requestConfig: { elt: slot.querySelector("[role=dialog]"), verb: "post" } } });
    c.beforeSwap({ detail: { target: slot, xhr } });
    slot.innerHTML = "";
    c.afterSwap({ detail: { target: slot, xhr } });
    expect(document.activeElement).toBe(input);
    expect(input.value).toBe("Unchanged text");
    expect(document.querySelector("textarea").value).toBe("Outer draft");
});
it("focuses the first invalid field after a failed POST swap without releasing modality", () => {
    const { open, slot } = setup();
    open();
    open(true);
    expect(document.activeElement.id).toBe("error");
    expect(document.getElementById("event-create-form").hasAttribute("inert")).toBe(true);
    expect(slot.closest("[inert]")).toBeNull();
});
it("cleans up its own inert on component destruction", () => {
    const { c, open } = setup();
    open();
    c.destroy();
    expect(document.getElementById("event-create-form").hasAttribute("inert")).toBe(false);
    expect(document.querySelector("nav").hasAttribute("inert")).toBe(true);
});

it("handles swap and keyboard bindings through the real CSP Alpine slot", async () => {
    const page = readFileSync("src/templates/core/events/create.html", "utf8");
    const slotMarkup = page.match(/<div id="client-inline-create-modal"[\s\S]*?<\/div>/)[0];
    document.body.innerHTML = '<main><form id="event-create-form"><input role="combobox"><button id="create">Create</button></form>' + slotMarkup + '</main>';
    Alpine.initTree(document.body);
    const slot = document.getElementById("client-inline-create-modal");
    const opener = document.getElementById("create");
    opener.focus();
    const swapEvent = (name) => new CustomEvent("htmx:" + name, { bubbles: true, detail: { target: slot, requestConfig: { elt: opener } } });
    slot.dispatchEvent(swapEvent("before-swap"));
    slot.innerHTML = '<div role="dialog" tabindex="-1"><input autofocus id="first"><button id="last" data-inline-client-cancel>Cancel</button></div>';
    slot.dispatchEvent(swapEvent("after-swap"));
    await Alpine.nextTick();
    expect(document.activeElement.id).toBe("first");
    expect(document.getElementById("event-create-form").hasAttribute("inert")).toBe(true);
    document.getElementById("last").focus();
    document.activeElement.dispatchEvent(key("Tab"));
    expect(document.activeElement.id).toBe("first");
    slot.dispatchEvent(swapEvent("before-swap"));
    slot.innerHTML = "";
    slot.dispatchEvent(swapEvent("after-swap"));
    expect(document.activeElement).toBe(opener);
    expect(document.getElementById("event-create-form").hasAttribute("inert")).toBe(false);
    window.dispatchEvent(new CustomEvent("client-inline-created"));
    await Alpine.nextTick();
    // Raw server events cannot revive a closed dialog or change its focus.
    expect(document.activeElement).toBe(opener);
});
