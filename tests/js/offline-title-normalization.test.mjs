// Refs #2625: the work item title edge trim must run in linear time and keep
// the exact str.isspace() semantics introduced with #2545.
import { beforeAll, describe, expect, it } from "vitest";

let fields;
beforeAll(async () => {
    window.Alpine = { data: () => {} };
    await import("../../src/static/js/offline-form-fields.js");
    fields = window.offlineFormFields;
});

// Independent oracle for Python's str.isspace() on the BMP: the JS \s class
// minus U+FEFF, plus the C0 separators U+001C..U+001F and U+0085.
function pythonIsSpace(codePoint) {
    const char = String.fromCodePoint(codePoint);
    if (codePoint === 0xfeff) return false;
    if (codePoint >= 0x1c && codePoint <= 0x1f) return true;
    if (codePoint === 0x85) return true;
    return /\s/.test(char);
}

function trimmed(title) {
    return fields.workItemFormData({ title }).title;
}

describe("work item title edge normalization", () => {
    it("matches str.isspace() for every BMP code point at both edges", () => {
        for (let codePoint = 0; codePoint <= 0xffff; codePoint++) {
            if (codePoint >= 0xd800 && codePoint <= 0xdfff) continue;
            const char = String.fromCodePoint(codePoint);
            const expected = pythonIsSpace(codePoint) ? "x" : `${char}x${char}`;
            expect(trimmed(`${char}x${char}`), `U+${codePoint.toString(16)}`).toBe(expected);
        }
    });

    it("keeps interior whitespace and trims mixed edge runs", () => {
        expect(trimmed(" \t　\u0085a  b \u001f\n")).toBe("a  b");
        expect(trimmed("﻿ a ﻿")).toBe("﻿ a ﻿");
    });

    it("turns whitespace-only, empty and missing titles into an empty string", () => {
        expect(trimmed(" \t\n 　")).toBe("");
        expect(trimmed("")).toBe("");
        expect(trimmed(undefined)).toBe("");
        expect(trimmed(null)).toBe("");
    });

    it("keeps astral characters at the edges intact", () => {
        expect(trimmed(" \u{1F600}x\u{1F600} ")).toBe("\u{1F600}x\u{1F600}");
    });

    it("normalizes a long title with a huge interior whitespace run quickly", () => {
        const title = "a" + " ".repeat(60000) + "b";
        const started = performance.now();
        const result = trimmed(title);
        const elapsed = performance.now() - started;
        expect(result).toBe(title);
        expect(elapsed).toBeLessThan(250);
    });

    it("normalizes a whitespace-only paste of any size quickly", () => {
        const started = performance.now();
        expect(trimmed(" ".repeat(60000))).toBe("");
        expect(performance.now() - started).toBeLessThan(250);
    });

    it("rejects an over-long title with a huge interior whitespace run without stalling", () => {
        document.body.innerHTML = '<div x-data><input data-wi-field="title" data-wi-error-id="e"><p id="e" hidden></p></div>';
        const root = document.querySelector("[x-data]");
        const values = fields.emptyWorkItemValues();
        values.title = "a" + " ".repeat(60000) + "b";
        const started = performance.now();
        expect(fields.validateWorkItemForm(values, root, false)).toBe(false);
        expect(performance.now() - started).toBeLessThan(250);
        expect(document.getElementById("e").hidden).toBe(false);
        expect(document.querySelector("input").getAttribute("aria-invalid")).toBe("true");
    });
});
