import { afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";

beforeAll(async () => {
    await import("../../src/static/js/double-submit.js");
});
beforeEach(() => vi.useFakeTimers());
afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    document.body.innerHTML = "";
});
function form(attributes = "") {
    document.body.innerHTML = `<form ${attributes}><button type="submit" name="format" value="csv">CSV</button><button type="submit" name="format" value="pdf">PDF</button><button type="submit" disabled>Unavailable</button></form>`;
    return document.querySelector("form");
}
function submit(target) {
    const event = new Event("submit", { bubbles: true, cancelable: true });
    target.dispatchEvent(event);
    return event;
}
it("releases download buttons after a short duplicate-click guard", () => {
    const target = form("data-download-form");
    expect(submit(target).defaultPrevented).toBe(false);
    expect(target.querySelector("button").disabled).toBe(false);
    expect(submit(target).defaultPrevented).toBe(true);
    vi.advanceTimersByTime(0);
    expect(target.querySelector("button").disabled).toBe(true);
    vi.advanceTimersByTime(4999);
    expect(target.querySelector("button").disabled).toBe(true);
    expect(submit(target).defaultPrevented).toBe(true);
    vi.advanceTimersByTime(1);
    expect(target.querySelector("button").disabled).toBe(false);
    expect(target.querySelectorAll("button")[1].disabled).toBe(false);
    expect(target.querySelectorAll("button")[2].disabled).toBe(true);
    expect(submit(target).defaultPrevented).toBe(false);
});
it("keeps regular forms locked until pageshow", () => {
    const target = form();
    submit(target);
    vi.advanceTimersByTime(5000);
    expect(submit(target).defaultPrevented).toBe(true);
    window.dispatchEvent(new Event("pageshow"));
    expect(submit(target).defaultPrevented).toBe(false);
});
it("releases submissions cancelled by a target listener", () => {
    const target = form("data-download-form");
    target.addEventListener("submit", (event) => event.preventDefault(), { once: true });
    submit(target);
    vi.advanceTimersByTime(0);
    expect(target.querySelector("button").disabled).toBe(false);
    expect(submit(target).defaultPrevented).toBe(false);
});
it("leaves HTMX forms to their own lifecycle", () => {
    const target = form('hx-get="/export/" data-download-form');
    submit(target);
    vi.advanceTimersByTime(1000);
    expect(target.dataset.submitting).toBeUndefined();
    expect(submit(target).defaultPrevented).toBe(false);
});
