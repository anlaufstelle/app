import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const factories = {};
const stores = {};

beforeAll(async () => {
    window.Alpine = {
        data: vi.fn((name, factory) => {
            factories[name] = factory;
        }),
        store: vi.fn((name, value) => {
            stores[name] = value;
        }),
    };
    await import("../../src/static/js/alpine/widgets.js");
    document.dispatchEvent(new Event("alpine:init"));
});

beforeEach(() => {
    window.localStorage.clear();
    stores.zeitstromCollapse.allExpanded = false;
});

describe.each(["expandableCard", "expandableActivityCard"])("%s", (name) => {
    it("uses a one-shot live-swap marker before the global collapse default", () => {
        const root = document.createElement("div");
        root.dataset.zeitstromExpanded = "true";
        const component = factories[name]();
        component.$el = root;
        component.$store = stores;

        component.applyGlobalCollapse();

        expect(component.expanded).toBe(true);
        expect(root.hasAttribute("data-zeitstrom-expanded")).toBe(false);
    });
});
