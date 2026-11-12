// Filter panel of the contacts list: focus on open and return on close, Escape,
// outside click, the "Zeitraum" link without a preset, state kept across the
// out-of-band form swap, the JS-only hiding of the submit fallback and the
// delegated persistence that keeps working after that swap.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

beforeAll(async () => {
    await import("../../src/static/js/filter-persistence.js");
});

const FORM = (extra = "") => `
<form id="event-filters" data-filter-persist data-filter-persist-url>
  <input id="event-q" name="q" type="search" value="" data-filter-persist-exclude>
  <input type="hidden" name="range" value="">
  <a data-period-segment data-period="custom" href="/events/#event-date_from">Zeitraum</a>
  <details data-filter-panel ${extra}>
    <summary id="event-filter-panel-toggle">Filter</summary>
    <div data-filter-panel-body>
      <input id="event-date_from" name="date_from" type="date" data-filter-persist-exclude>
      <select id="event-doc_type" name="doc_type"><option value="">All</option><option value="t1">T1</option></select>
      <button id="event-submit" type="submit" data-filter-submit-fallback>Filtern</button>
    </div>
  </details>
</form>`;
const mount = (extra) => {
    document.body.innerHTML = FORM(extra);
    return {
        panel: document.querySelector("details[data-filter-panel]"),
        summary: document.getElementById("event-filter-panel-toggle"),
    };
};
beforeEach(() => {
    document.body.innerHTML = "";
    sessionStorage.clear();
    vi.stubGlobal("htmx", {});
});
afterEach(() => vi.unstubAllGlobals());
const toggle = (panel) => panel.dispatchEvent(new Event("toggle"));

describe("focus", () => {
    it("moves focus to the first field when the panel opens", () => {
        const { panel, summary } = mount();
        summary.focus();
        panel.open = true;
        toggle(panel);
        expect(document.activeElement.id).toBe("event-date_from");
    });
    it("does not pull focus back when it is already inside the panel (after a swap)", () => {
        const { panel } = mount("open");
        document.getElementById("event-doc_type").focus();
        toggle(panel);
        expect(document.activeElement.id).toBe("event-doc_type");
    });
    it("returns focus to the toggle on Escape and closes the panel", () => {
        const { panel } = mount();
        panel.open = true;
        const select = document.getElementById("event-doc_type");
        select.focus();
        select.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
        expect(panel.open).toBe(false);
        expect(document.activeElement.id).toBe("event-filter-panel-toggle");
    });
    it("ignores Escape while the panel is closed", () => {
        const { panel } = mount();
        const keydown = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
        document.getElementById("event-q").dispatchEvent(keydown);
        expect(panel.open).toBe(false);
        expect(keydown.defaultPrevented).toBe(false);
    });
    it("closes on an outside click but not on a click inside", () => {
        const { panel } = mount();
        panel.open = true;
        document.getElementById("event-doc_type").dispatchEvent(new MouseEvent("click", { bubbles: true }));
        expect(panel.open).toBe(true);
        document.getElementById("event-q").dispatchEvent(new MouseEvent("click", { bubbles: true }));
        expect(panel.open).toBe(false);
    });
});

describe("Zeitraum link without a preset", () => {
    it("opens the panel and focuses Von instead of navigating", () => {
        const { panel } = mount();
        const link = document.querySelector("[data-period='custom']");
        const click = new MouseEvent("click", { bubbles: true, cancelable: true });
        link.dispatchEvent(click);
        expect(click.defaultPrevented).toBe(true);
        expect(panel.open).toBe(true);
        expect(document.activeElement.id).toBe("event-date_from");
    });
    it("leaves a link that carries a request alone", () => {
        mount();
        const link = document.querySelector("[data-period='custom']");
        link.setAttribute("hx-get", "/events/?date_from=2026-10-05");
        const click = new MouseEvent("click", { bubbles: true, cancelable: true });
        link.dispatchEvent(click);
        expect(click.defaultPrevented).toBe(false);
    });
});

describe("out-of-band form swap", () => {
    function swap(oldForm, incomingHtml) {
        const fragment = document.createDocumentFragment();
        const holder = document.createElement("div");
        holder.innerHTML = incomingHtml;
        fragment.appendChild(holder.firstElementChild);
        oldForm.dispatchEvent(
            new CustomEvent("htmx:oobBeforeSwap", { bubbles: true, detail: { target: oldForm, fragment } }),
        );
        return fragment;
    }
    it("keeps an open panel open", () => {
        const { panel } = mount();
        panel.open = true;
        const fragment = swap(document.getElementById("event-filters"), FORM());
        expect(fragment.querySelector("details").open).toBe(true);
    });
    it("keeps what is being typed in the focused search field", () => {
        mount();
        const q = document.getElementById("event-q");
        q.focus();
        q.value = "abc";
        const fragment = swap(document.getElementById("event-filters"), FORM());
        expect(fragment.querySelector("#event-q").value).toBe("abc");
    });
    it("does not touch other forms", () => {
        document.body.innerHTML = '<form id="other"><input id="x" value="1"></form>';
        const fragment = swap(document.getElementById("other"), '<form id="other"><input id="x" value="2"></form>');
        expect(fragment.querySelector("#x").value).toBe("2");
    });
    it("hides the submit fallback in the incoming form when htmx is present", () => {
        mount();
        const fragment = swap(document.getElementById("event-filters"), FORM());
        expect(fragment.querySelector("[data-filter-submit-fallback]").hidden).toBe(true);
    });
});

describe("submit fallback", () => {
    it("is hidden only when htmx can filter immediately", () => {
        mount();
        document.dispatchEvent(new Event("DOMContentLoaded"));
        expect(document.getElementById("event-submit").hidden).toBe(true);
        vi.stubGlobal("htmx", undefined);
        mount();
        document.dispatchEvent(new Event("DOMContentLoaded"));
        expect(document.getElementById("event-submit").hidden).toBe(false);
    });
});

describe("persistence after the form was replaced", () => {
    it("saves a changed select of the new form without its own listeners", () => {
        mount();
        document.dispatchEvent(new Event("DOMContentLoaded"));
        document.body.innerHTML = FORM(); // simulates the out-of-band swap
        const select = document.getElementById("event-doc_type");
        select.value = "t1";
        select.dispatchEvent(new Event("change", { bubbles: true }));
        expect(JSON.parse(sessionStorage.getItem(`filters:${window.location.pathname}`))).toEqual({ doc_type: "t1" });
    });
    it("saves the hidden preset after a swap and never the search or the dates", () => {
        mount();
        document.dispatchEvent(new Event("DOMContentLoaded"));
        document.querySelector("input[name=range]").value = "this_week";
        document.getElementById("event-q").value = "secret";
        document.getElementById("event-date_from").value = "2026-10-01";
        document
            .getElementById("event-filters")
            .dispatchEvent(new CustomEvent("htmx:oobAfterSwap", { bubbles: true }));
        expect(JSON.parse(sessionStorage.getItem(`filters:${window.location.pathname}`))).toEqual({ range: "this_week" });
    });
});
