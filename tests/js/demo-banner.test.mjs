// Refs #2663: the demo notice folds its details on small screens. The summary
// line itself is never hidden by this script (public-demo transparency).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const KEY = "anlaufstelle.demoBanner.open";

const MARKUP = `
    <div id="demo-banner" data-testid="demo-banner" class="group">
        <span data-demo-banner-summary>Demo · synthetische Daten · Reset 18:00</span>
        <button type="button" hidden data-demo-banner-toggle aria-expanded="true"
                aria-controls="demo-banner-details">Demo · synthetische Daten · Reset 18:00</button>
        <span id="demo-banner-details">Details</span>
    </div>
`;

async function load({ markup = MARKUP } = {}) {
    vi.resetModules();
    document.body.innerHTML = markup;
    await import("../../src/static/js/demo-banner.js");
    return {
        banner: document.getElementById("demo-banner"),
        toggle: document.querySelector("[data-demo-banner-toggle]"),
        summary: document.querySelector("[data-demo-banner-summary]"),
    };
}

describe("demo-banner.js", () => {
    beforeEach(() => {
        window.sessionStorage.clear();
    });
    afterEach(() => {
        vi.restoreAllMocks();
        document.body.innerHTML = "";
    });

    it("starts folded and swaps the static summary for the toggle", async () => {
        const { banner, toggle, summary } = await load();
        expect(banner.hasAttribute("data-collapsed")).toBe(true);
        expect(toggle.hidden).toBe(false);
        expect(toggle.getAttribute("aria-expanded")).toBe("false");
        expect(summary.hidden).toBe(true);
    });

    it("never hides the banner or the toggle text itself", async () => {
        const { banner, toggle } = await load();
        expect(banner.hidden).toBe(false);
        expect(toggle.textContent).toContain("Reset 18:00");
    });

    it("unfolds on click, remembers it for the session, and folds again", async () => {
        const { banner, toggle } = await load();
        toggle.click();
        expect(banner.hasAttribute("data-collapsed")).toBe(false);
        expect(toggle.getAttribute("aria-expanded")).toBe("true");
        expect(window.sessionStorage.getItem(KEY)).toBe("1");
        toggle.click();
        expect(banner.hasAttribute("data-collapsed")).toBe(true);
        expect(toggle.getAttribute("aria-expanded")).toBe("false");
        expect(window.sessionStorage.getItem(KEY)).toBe("0");
    });

    it("restores the unfolded state on the next page of the same session", async () => {
        window.sessionStorage.setItem(KEY, "1");
        const { banner, toggle } = await load();
        expect(banner.hasAttribute("data-collapsed")).toBe(false);
        expect(toggle.getAttribute("aria-expanded")).toBe("true");
    });

    it("still works when sessionStorage throws", async () => {
        vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
            throw new Error("denied");
        });
        vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
            throw new Error("denied");
        });
        const { banner, toggle } = await load();
        expect(banner.hasAttribute("data-collapsed")).toBe(true);
        toggle.click();
        expect(banner.hasAttribute("data-collapsed")).toBe(false);
        expect(toggle.getAttribute("aria-expanded")).toBe("true");
    });

    it("does nothing without the markup (non-demo pages)", async () => {
        await expect(load({ markup: "<main></main>" })).resolves.toBeDefined();
    });

    it("leaves the full notice untouched when the toggle is missing", async () => {
        const { banner, summary } = await load({
            markup: '<div id="demo-banner"><span data-demo-banner-summary>x</span></div>',
        });
        expect(banner.hasAttribute("data-collapsed")).toBe(false);
        expect(summary.hidden).toBe(false);
    });
});
