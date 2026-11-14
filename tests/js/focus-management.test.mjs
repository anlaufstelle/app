// Refs #1059: Der HTMX-Fokus-Manager war zweistufig (Opt-in-Element ODER
// Swap-Container). Die im Ticket beschriebene mittlere Stufe — "sonst das
// erste invalide Feld" — fehlte: nach einem Server-Re-Render mit
// Validierungsfehlern landete der Fokus auf dem Container, und der Fehler
// selbst war nur durch Weiterlesen zu finden.
//
// Dieser Test pinnt alle drei Stufen in ihrer Reihenfolge sowie die beiden
// vorgelagerten Ausstiege (Opt-out, Live-Such-Schutz), damit die neue Stufe
// die bestehenden Zusagen nicht verschiebt.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

beforeAll(async () => {
    await import("../../src/static/js/focus-management.js");
});

beforeEach(() => {
    document.body.innerHTML = "";
    document.body.removeAttribute("tabindex");
});

/** Feuert ``htmx:afterSwap`` so, wie htmx es tut: am body, mit ``detail.target``. */
function afterSwap(target) {
    document.body.dispatchEvent(
        new CustomEvent("htmx:afterSwap", { bubbles: true, detail: { target } })
    );
}

function container(html, attrs = {}) {
    const div = document.createElement("div");
    div.id = "swap-target";
    Object.entries(attrs).forEach(([key, value]) => div.setAttribute(key, value));
    div.innerHTML = html;
    document.body.appendChild(div);
    return div;
}

describe("Stufe 1 — explizites Opt-in", () => {
    it("fokussiert [data-hx-focus] vor allem anderen", () => {
        const target = container(
            '<input id="invalid" aria-invalid="true"><input id="opt-in" data-hx-focus>'
        );

        afterSwap(target);

        expect(document.activeElement.id).toBe("opt-in");
        // Auch das Opt-in-Ziel bleibt in der Tab-Reihenfolge.
        expect(document.getElementById("opt-in").hasAttribute("tabindex")).toBe(false);
    });

    it("fokussiert [autofocus] ebenso", () => {
        const target = container('<input id="auto" autofocus><input id="invalid" aria-invalid="true">');

        afterSwap(target);

        expect(document.activeElement.id).toBe("auto");
    });
});

describe("Stufe 2 — erstes invalides Feld", () => {
    it("fokussiert das erste aria-invalid-Feld, wenn kein Opt-in existiert", () => {
        const target = container(
            '<input id="ok"><input id="erstes" aria-invalid="true"><input id="zweites" aria-invalid="true">'
        );

        afterSwap(target);

        expect(document.activeElement.id).toBe("erstes");
    });

    it("nimmt das invalide Feld NICHT aus der Tab-Reihenfolge", () => {
        // Refs #1059 (Cross-Review B2', HOCH): ein programmatisch gesetztes
        // tabindex="-1" haette das Feld nach dem Verlassen unerreichbar
        // gemacht — native Controls sind ohnehin fokussierbar.
        const target = container('<input id="erstes" aria-invalid="true">');

        afterSwap(target);

        const feld = document.getElementById("erstes");
        expect(document.activeElement).toBe(feld);
        expect(feld.hasAttribute("tabindex")).toBe(false);
    });

    it("erkennt ein invalides Feld, das der Container SELBST ist (outerHTML-Swap)", () => {
        const field = document.createElement("input");
        field.id = "feld";
        field.setAttribute("aria-invalid", "true");
        document.body.appendChild(field);

        afterSwap(field);

        expect(document.activeElement.id).toBe("feld");
        expect(field.hasAttribute("tabindex")).toBe(false);
    });

    it("ignoriert aria-invalid=false", () => {
        const target = container('<input id="sauber" aria-invalid="false">');

        afterSwap(target);

        expect(document.activeElement.id).toBe("swap-target");
    });
});

describe("Stufe 3 — Container-Fallback", () => {
    it("fokussiert den Swap-Container und macht ihn dafuer fokussierbar", () => {
        const target = container("<p>Neuer Inhalt</p>");

        afterSwap(target);

        expect(document.activeElement.id).toBe("swap-target");
        expect(target.getAttribute("tabindex")).toBe("-1");
    });

    it("laesst ein vorhandenes tabindex unangetastet", () => {
        const target = container("<p>x</p>", { tabindex: "0" });

        afterSwap(target);

        expect(target.getAttribute("tabindex")).toBe("0");
    });
});

describe("Die vorgelagerten Ausstiege bleiben unveraendert", () => {
    it("data-hx-no-focus am Swap-Ziel unterdrueckt auch Stufe 2", () => {
        const target = container('<input id="invalid" aria-invalid="true">', {
            "data-hx-no-focus": "",
        });

        afterSwap(target);

        expect(document.activeElement).toBe(document.body);
    });

    it("Live-Suche: ein fokussiertes Textfeld ausserhalb des Ziels behaelt den Fokus", () => {
        const suche = document.createElement("input");
        suche.type = "search";
        suche.id = "suche";
        document.body.appendChild(suche);
        const target = container('<input id="invalid" aria-invalid="true">');
        suche.focus();

        afterSwap(target);

        expect(document.activeElement.id).toBe("suche");
    });

    it("laesst einen Fokus in Ruhe, der bereits IM getauschten Ziel liegt", () => {
        const target = container('<button id="drin"></button><input id="invalid" aria-invalid="true">');
        document.getElementById("drin").focus();

        afterSwap(target);

        expect(document.activeElement.id).toBe("drin");
    });

    it("greift einen outerHTML-Swap ueber die id wieder auf", () => {
        const alt = container('<input id="invalid" aria-invalid="true">');
        alt.remove();
        const neu = container('<input id="invalid" aria-invalid="true">');

        afterSwap(alt);

        expect(neu.contains(document.activeElement)).toBe(true);
        expect(document.activeElement.id).toBe("invalid");
    });
});

describe("Self-refresh in the background (condition board after a field board action)", () => {
    const SELF_REFRESH_ATTR = "data-hx-no-focus-self-refresh";

    /** Fires ``htmx:afterSwap`` with the request config htmx 2 reports (``requestConfig.elt``). */
    function afterSwapFrom(target, requestElt) {
        document.body.dispatchEvent(
            new CustomEvent("htmx:afterSwap", {
                bubbles: true,
                detail: { target, requestConfig: { elt: requestElt } },
            })
        );
    }

    /** Builds the page state after an outerHTML swap: old node detached, new twin connected. */
    function swappedBoard(attrs) {
        const old = document.createElement("div");
        old.id = "condition-board";
        Object.entries(attrs).forEach(([key, value]) => old.setAttribute(key, value));
        document.body.appendChild(old);
        const twin = document.createElement("div");
        twin.id = "condition-board";
        Object.entries(attrs).forEach(([key, value]) => twin.setAttribute(key, value));
        twin.innerHTML = '<button id="inside">Add</button>';
        old.replaceWith(twin);
        return { old, twin };
    }

    function fieldBoardWithFocus() {
        const board = document.createElement("div");
        board.id = "field-board";
        board.setAttribute("tabindex", "-1");
        board.innerHTML = '<button id="field-action">Assign</button>';
        document.body.appendChild(board);
        board.focus();
        return board;
    }

    it("keeps the focus on the field board when the condition board reloads itself", () => {
        const fieldBoard = fieldBoardWithFocus();
        const { old, twin } = swappedBoard({ [SELF_REFRESH_ATTR]: "" });

        afterSwapFrom(old, old);

        expect(document.activeElement).toBe(fieldBoard);
        expect(twin.hasAttribute("tabindex")).toBe(false);
    });

    it("keeps the focus on a control inside the field board as well", () => {
        fieldBoardWithFocus();
        const control = document.getElementById("field-action");
        control.focus();
        const { old } = swappedBoard({ [SELF_REFRESH_ATTR]: "" });

        afterSwapFrom(old, old);

        expect(document.activeElement).toBe(control);
    });

    it("still restores a focus that the reload took away from inside the board", () => {
        const { old, twin } = swappedBoard({ [SELF_REFRESH_ATTR]: "" });

        afterSwapFrom(old, old);

        expect(document.activeElement).toBe(twin);
    });

    it("keeps guiding the focus for an action inside the condition board", () => {
        const fieldBoard = fieldBoardWithFocus();
        const { old, twin } = swappedBoard({ [SELF_REFRESH_ATTR]: "" });
        const form = document.createElement("form");

        afterSwapFrom(old, form);

        expect(fieldBoard).not.toBe(document.activeElement);
        expect(document.activeElement).toBe(twin);
        expect(twin.getAttribute("tabindex")).toBe("-1");
    });

    it("keeps the focus where it is when a feed marked 'always' reloads with the focus on the body", () => {
        const { old, twin } = swappedBoard({ [SELF_REFRESH_ATTR]: "always" });
        expect(document.activeElement).toBe(document.body);

        afterSwapFrom(old, old);

        expect(document.activeElement).toBe(document.body);
        expect(twin.hasAttribute("tabindex")).toBe(false);
    });

    it("keeps the focus on a control outside a feed marked 'always' as well", () => {
        const fieldBoard = fieldBoardWithFocus();
        const { old } = swappedBoard({ [SELF_REFRESH_ATTR]: "always" });

        afterSwapFrom(old, old);

        expect(document.activeElement).toBe(fieldBoard);
    });

    it("an 'always' feed still guides the focus for a swap started from another element", () => {
        const { old, twin } = swappedBoard({ [SELF_REFRESH_ATTR]: "always" });
        const select = document.createElement("select");

        afterSwapFrom(old, select);

        expect(document.activeElement).toBe(twin);
    });

    it("only a target with the attribute is exempt from the reload rule", () => {
        const fieldBoard = fieldBoardWithFocus();
        const { old, twin } = swappedBoard({});

        afterSwapFrom(old, old);

        expect(fieldBoard).not.toBe(document.activeElement);
        expect(document.activeElement).toBe(twin);
    });
});

describe("Background reload of an 'always' feed with the focus inside it", () => {
    const SELF_REFRESH_ATTR = "data-hx-no-focus-self-refresh";
    let scrollToSpy;

    beforeEach(() => {
        scrollToSpy = vi.fn();
        window.scrollTo = scrollToSpy;
        Object.defineProperty(window, "scrollX", { configurable: true, value: 0 });
        Object.defineProperty(window, "scrollY", { configurable: true, value: 640 });
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    function beforeSwapOn(target) {
        document.body.dispatchEvent(
            new CustomEvent("htmx:beforeSwap", { bubbles: true, detail: { target } })
        );
    }

    function afterSwapFrom(target, requestElt) {
        document.body.dispatchEvent(
            new CustomEvent("htmx:afterSwap", {
                bubbles: true,
                detail: { target, requestConfig: { elt: requestElt } },
            })
        );
    }

    function feed(value) {
        const el = document.createElement("div");
        el.id = "feed-list";
        el.setAttribute(SELF_REFRESH_ATTR, value);
        el.innerHTML =
            '<button id="other-card" aria-controls="detail-other">Other</button>' +
            '<button id="the-card" aria-controls="detail-1">Card</button>';
        document.body.appendChild(el);
        return el;
    }

    /** What an innerHTML reload does: the old controls disappear, twins with the same keys arrive. */
    function reload(el, html) {
        beforeSwapOn(el);
        el.innerHTML = html;
        afterSwapFrom(el, el);
    }

    const TWIN_HTML =
        '<button aria-controls="detail-other">Other</button><button aria-controls="detail-1">Card</button>';

    it("hands the focus to the same control in the new content without scrolling", () => {
        const el = feed("always");
        el.querySelector('[aria-controls="detail-1"]').focus();
        const focusSpy = vi.spyOn(HTMLElement.prototype, "focus");

        reload(el, TWIN_HTML);

        const twin = el.querySelector('[aria-controls="detail-1"]');
        expect(document.activeElement).toBe(twin);
        expect(focusSpy).toHaveBeenCalledWith({ preventScroll: true });
        expect(focusSpy).not.toHaveBeenCalledWith({ preventScroll: false });
    });

    it("falls back to the feed itself when the control is gone", () => {
        const el = feed("always");
        el.querySelector('[aria-controls="detail-1"]').focus();

        reload(el, "<p>Nothing left</p>");

        expect(document.activeElement).toBe(el);
        expect(el.getAttribute("tabindex")).toBe("-1");
    });

    it("puts the scroll position from before the reload back", () => {
        const el = feed("always");
        el.querySelector('[aria-controls="detail-1"]').focus();
        beforeSwapOn(el);
        el.innerHTML = TWIN_HTML;
        Object.defineProperty(window, "scrollY", { configurable: true, value: 2300 });

        afterSwapFrom(el, el);

        expect(scrollToSpy).toHaveBeenCalledWith(0, 640);
    });

    it("does not scroll when the position did not change", () => {
        const el = feed("always");
        el.querySelector('[aria-controls="detail-1"]').focus();

        reload(el, TWIN_HTML);

        expect(scrollToSpy).not.toHaveBeenCalled();
    });

    it("takes no focus when nothing held it before the reload", () => {
        const el = feed("always");
        expect(document.activeElement).toBe(document.body);

        reload(el, TWIN_HTML);

        expect(document.activeElement).toBe(document.body);
    });

    it("takes no focus when the focus sat outside the feed", () => {
        const outside = document.createElement("button");
        document.body.appendChild(outside);
        const el = feed("always");
        outside.focus();

        reload(el, TWIN_HTML);

        expect(document.activeElement).toBe(outside);
    });

    /** A feed whose focused control is a link; the reload brings the given markup. */
    function feedWithFocusedLink(href) {
        const el = feed("always");
        el.innerHTML = `<a id="focused-link" href="${href}">Open</a>`;
        el.querySelector("a").focus();
        return el;
    }

    /** Pretend every element is laid out (jsdom has no layout) unless the test says otherwise. */
    function renderEverything() {
        vi.spyOn(Element.prototype, "getClientRects").mockImplementation(() => [{}]);
    }

    it("falls back to the feed when the twin cannot take the focus (collapsed detail)", () => {
        const el = feed("always");
        el.querySelector('[aria-controls="detail-1"]').focus();
        const realFocus = HTMLElement.prototype.focus;
        vi.spyOn(HTMLElement.prototype, "focus").mockImplementation(function (options) {
            if (this.getAttribute("aria-controls") === "detail-1") {
                return; // a control inside a hidden region does not take the focus
            }
            return realFocus.call(this, options);
        });

        reload(el, TWIN_HTML);

        expect(document.activeElement).toBe(el);
        expect(el.getAttribute("tabindex")).toBe("-1");
    });

    it("takes back the tabindex it set on the container once the focus leaves it", () => {
        const el = feed("always");
        el.querySelector('[aria-controls="detail-1"]').focus();
        const realFocus = HTMLElement.prototype.focus;
        vi.spyOn(HTMLElement.prototype, "focus").mockImplementation(function (options) {
            if (this.getAttribute("aria-controls") === "detail-1") {
                return; // a control inside a hidden region does not take the focus
            }
            return realFocus.call(this, options);
        });
        reload(el, TWIN_HTML);
        expect(document.activeElement).toBe(el);
        expect(el.getAttribute("tabindex")).toBe("-1");
        vi.restoreAllMocks();

        el.querySelector('[aria-controls="detail-other"]').focus();

        expect(el.hasAttribute("tabindex")).toBe(false);
    });

    it("keeps a tabindex the container already had", () => {
        const el = feed("always");
        el.setAttribute("tabindex", "-1");
        el.querySelector('[aria-controls="detail-1"]').focus();
        reload(el, '<p>nothing to focus</p>');
        expect(document.activeElement).toBe(el);

        const outside = document.createElement("button");
        document.body.appendChild(outside);
        outside.focus();

        expect(el.getAttribute("tabindex")).toBe("-1");
    });

    it("takes a link twin when exactly one rendered link carries the target", () => {
        renderEverything();
        const el = feedWithFocusedLink("/clients/7/");

        reload(el, '<a href="/other/">Other</a><a href="/clients/7/">Open</a>');

        expect(document.activeElement).toBe(el.querySelector('a[href="/clients/7/"]'));
    });

    it("does not guess between several links with the same target", () => {
        renderEverything();
        const el = feedWithFocusedLink("/clients/7/");

        reload(el, '<a href="/clients/7/">First</a><a href="/clients/7/">Second</a>');

        expect(document.activeElement).toBe(el);
    });

    it("does not take a link twin that is not rendered", () => {
        const el = feedWithFocusedLink("/clients/7/");

        reload(el, '<a href="/clients/7/">Hidden</a>');

        expect(document.activeElement).toBe(el);
    });

    it("forgets the focus snapshot of a swap that never reached afterSwap", () => {
        const el = feed("always");
        el.querySelector('[aria-controls="detail-1"]').focus();
        beforeSwapOn(el);
        document.body.dispatchEvent(new CustomEvent("htmx:responseError", { bubbles: true, detail: { target: el } }));
        document.activeElement.blur();

        // A later swap of the same target that did not announce itself with beforeSwap.
        el.innerHTML = TWIN_HTML;
        afterSwapFrom(el, el);

        expect(document.activeElement).toBe(document.body);
        expect(scrollToSpy).not.toHaveBeenCalled();
    });

    it("lets the live watch hand the focus on through the shared guard", () => {
        const el = feed("always");
        const card = el.querySelector('[aria-controls="detail-1"]');
        card.focus();
        const snapshot = window.backgroundSwapGuard.capture(el);
        el.innerHTML = TWIN_HTML; // htmx.swap fires no afterSwap with a target
        Object.defineProperty(window, "scrollY", { configurable: true, value: 2300 });

        window.backgroundSwapGuard.restore(el, snapshot);

        expect(document.activeElement).toBe(el.querySelector('[aria-controls="detail-1"]'));
        expect(scrollToSpy).toHaveBeenCalledWith(0, 640);
    });

    it("lets the live watch leave the focus alone when it sat outside the region", () => {
        const outside = document.createElement("button");
        document.body.appendChild(outside);
        const el = feed("always");
        outside.focus();
        const snapshot = window.backgroundSwapGuard.capture(el);
        el.innerHTML = TWIN_HTML;

        window.backgroundSwapGuard.restore(el, snapshot);

        expect(document.activeElement).toBe(outside);
    });

    it("lets the live watch respect the data-hx-no-focus opt-out on the region: no focus hand-over, scroll put back", () => {
        const el = feed("always");
        el.setAttribute("data-hx-no-focus", "");
        el.querySelector('[aria-controls="detail-1"]').focus();
        const snapshot = window.backgroundSwapGuard.capture(el);
        el.innerHTML = TWIN_HTML;
        Object.defineProperty(window, "scrollY", { configurable: true, value: 2300 });

        window.backgroundSwapGuard.restore(el, snapshot);

        expect(document.activeElement).toBe(document.body);
        expect(scrollToSpy).toHaveBeenCalledWith(0, 640);
    });

    it("lets the live watch respect the opt-out on the control that holds the focus, but still holds the page in place", () => {
        const holder = document.createElement("div");
        holder.setAttribute("data-hx-no-focus", "");
        holder.innerHTML = '<button id="opted-out">Trigger</button>';
        document.body.appendChild(holder);
        const el = feed("always");
        holder.querySelector("button").focus();
        const snapshot = window.backgroundSwapGuard.capture(el);
        el.innerHTML = TWIN_HTML;
        Object.defineProperty(window, "scrollY", { configurable: true, value: 2300 });

        window.backgroundSwapGuard.restore(el, snapshot);

        expect(document.activeElement.id).toBe("opted-out");
        expect(scrollToSpy).toHaveBeenCalledWith(0, 640);
    });

    it("does not scroll when an opted-out region swap left the position alone", () => {
        const el = feed("always");
        el.setAttribute("data-hx-no-focus", "");
        const snapshot = window.backgroundSwapGuard.capture(el);
        el.innerHTML = TWIN_HTML;

        window.backgroundSwapGuard.restore(el, snapshot);

        expect(scrollToSpy).not.toHaveBeenCalled();
    });

    it("leaves the condition board as before: the focus lost inside it goes to the board", () => {
        const el = feed("");
        el.id = "condition-board";
        el.querySelector('[aria-controls="detail-1"]').focus();

        reload(el, TWIN_HTML);

        expect(document.activeElement).toBe(el);
        expect(scrollToSpy).not.toHaveBeenCalled();
    });
});

// Die vormalige "Trefferansage (A11Y-035/036)"-Testgruppe pinnte
// ``_announceResultCount`` (DOM-Zeilenzaehlung ueber
// ``data-hx-announce-*``-Attribute). Refs #2155: der Pfad ist entfernt — die
// Trefferansage kommt jetzt serverseitig als ``hx-swap-oob``-Fragment aus den
// Listen-Templates selbst (echter ``paginator.count`` statt gezaehlter
// DOM-Zeilen). Die beiden hier mitgeprueften, weiterhin gueltigen Verhalten
// (``data-hx-no-focus`` unterdrueckt Fokus, Live-Suche behaelt den Fokus im
// Suchfeld) bleiben oben in "Die vorgelagerten Ausstiege bleiben
// unveraendert" abgedeckt — unabhaengig von der Ansage getestet.
