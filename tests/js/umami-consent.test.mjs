import { readFileSync } from "node:fs";
import { afterEach, expect, it } from "vitest";

const source = readFileSync("src/static/js/umami-consent.js", "utf8");
function loadPage() {
    document.body.innerHTML = '<div id="umami-consent" class="hidden" data-umami-website-id="demo" data-umami-src="https://analytics.example/main.js" data-umami-recorder-src="https://analytics.example/recorder.js"><button data-umami-consent-decline></button><button data-umami-consent-accept></button></div>';
    window.eval(source);
    return document.getElementById("umami-consent");
}
afterEach(() => {
    window.localStorage.clear();
    document.body.innerHTML = "";
    document.head.querySelectorAll("script").forEach((script) => script.remove());
});
it("persists rejection across pages and never loads either tracker", () => {
    window.localStorage.clear();
    const banner = loadPage();
    expect(banner.classList.contains("hidden")).toBe(false);
    banner.querySelector("[data-umami-consent-decline]").click();
    expect(window.localStorage.getItem("umami-consent")).toBe("denied");
    expect(loadPage().classList.contains("hidden")).toBe(true);
    expect(document.head.querySelectorAll("script")).toHaveLength(0);
});
it("loads analysis and recording only after consent", () => {
    window.localStorage.clear();
    const banner = loadPage();
    expect(document.head.querySelectorAll("script")).toHaveLength(0);
    banner.querySelector("[data-umami-consent-accept]").click();
    expect(window.localStorage.getItem("umami-consent")).toBe("granted");
    expect(document.head.querySelectorAll("script")).toHaveLength(2);
});
