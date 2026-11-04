import { beforeAll, describe, expect, it, vi } from "vitest";

const factories = {};

beforeAll(async () => {
    window.Alpine = {
        data: vi.fn((name, factory) => {
            factories[name] = factory;
        }),
    };
    await import("../../src/static/js/alpine/forms.js");
    document.dispatchEvent(new Event("alpine:init"));
});

describe("person autocomplete input id", () => {
    it("gives every instance its own input id that matches its listbox id", () => {
        const instances = [
            factories.clientAutocomplete(),
            factories.eventClientAutocomplete(),
            factories.clientAutocomplete(),
        ];
        const inputIds = instances.map((c) => c.inputId);

        expect(new Set(inputIds).size).toBe(inputIds.length);
        for (const c of instances) {
            expect(c.inputId).toMatch(/^client-autocomplete-input(-\d+)?$/);
            expect(c.inputId.replace("-input", "-list")).toBe(c.listboxId);
        }
    });
});
