// Refs #2136 (N-2): Mitschauen-/Shoulder-Surfing-Schutz.
//
// Das Modul haengt an drei Dingen, die im Python-Test nicht sichtbar sind: an
// der Reihenfolge (verdecken -> abmelden -> navigieren), an der Form des
// Logout-Beacons (der CSRF-Token muss IM BODY stehen, ein Beacon kann keine
// Header setzen) und an der Fokus-Rueckgabe nach der Sichtblende (WCAG 2.4.3).
// Dazu die Offline-Variante: ohne Server-Konfiguration liest das Modul den
// localStorage-Spiegel, den die Online-Seiten hinterlassen haben.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const LOGOUT_URL = "/logout/";
const DEFAULT_URL = "https://www.wikipedia.org/";
const STORAGE_KEY = "anlaufstelle.quickExit";

// Jede Modul-Instanz verdrahtet Listener an ``window``/``document``. Nach
// ``vi.resetModules()`` bleiben die ALTEN aktiv und wuerden mitfeuern (der
// zweite Test saehe zwei Beacons). Wir merken uns darum, was ein Import
// anhaengt, und raeumen es vor dem naechsten wieder ab. Gewrappt per
// ``Object.defineProperty``, nicht per ``vi.spyOn`` — ein Spy auf
// ``window.addEventListener`` haelt die echten Registrierungen nicht fest.
let _wired = [];

function _install(target, added) {
    const original = target.addEventListener.bind(target);
    Object.defineProperty(target, "addEventListener", {
        configurable: true,
        writable: true,
        value: (type, cb, opts) => {
            added.push([target, type, cb, opts]);
            original(type, cb, opts);
        },
    });
    return () => {
        Object.defineProperty(target, "addEventListener", {
            configurable: true,
            writable: true,
            value: original,
        });
    };
}

function _unwire() {
    _wired.forEach(([target, type, cb, opts]) => target.removeEventListener(type, cb, opts));
    _wired = [];
}

const MARKUP = `
    <a id="skip-link" href="#main-content">Zum Inhalt springen</a>
    <div id="quick-exit-bar">
        <button type="button" id="quick-exit-button">Schnell verlassen</button>
    </div>
    <nav id="nav-desktop"><a id="nav-link" href="/clients/">Personen</a></nav>
    <main id="main-content"><input id="feld"><button id="im-inhalt">Speichern</button></main>
    <div id="privacy-screen" hidden role="dialog">
        <div id="privacy-screen-panel">
            <p id="privacy-screen-title">Anlaufstelle</p>
            <button type="button" id="privacy-screen-resume">Fortfahren</button>
        </div>
    </div>
`;

/**
 * Modul frisch laden.
 *
 * @param {object} options
 * @param {object|null} options.dataset ``data-*``-Werte am body; ``null`` =
 *   Offline-Shell (das Template rendert dort keine Konfiguration).
 * @param {boolean} options.beacon ``navigator.sendBeacon`` bereitstellen.
 * @param {boolean} options.beaconOk Rueckgabewert von ``sendBeacon``.
 * @param {boolean} options.inert ``inert`` als unterstuetzt vortaeuschen.
 * @param {boolean} options.keyDropHangs ``clearSessionKey`` loest erst auf,
 *   wenn der Test ``resolveKeyDrop()`` ruft (bzw. der Deckel greift).
 */
async function freshLoad({ dataset = {}, beacon = true, beaconOk = true, inert = true, keyDropHangs = false } = {}) {
    _unwire();
    vi.resetModules();

    document.body.innerHTML = MARKUP;
    Object.keys(document.body.dataset).forEach((key) => delete document.body.dataset[key]);
    if (dataset) {
        Object.entries(dataset).forEach(([key, value]) => {
            document.body.dataset[key] = value;
        });
    }

    if (inert) {
        Object.defineProperty(HTMLElement.prototype, "inert", { configurable: true, writable: true, value: false });
    } else {
        delete HTMLElement.prototype.inert;
    }

    const replace = vi.fn();
    const reload = vi.fn();
    Object.defineProperty(window, "location", {
        configurable: true,
        writable: true,
        value: { href: "http://localhost/clients/", pathname: "/clients/", replace, reload },
    });

    const sendBeacon = vi.fn(() => beaconOk);
    if (beacon) {
        Object.defineProperty(navigator, "sendBeacon", { configurable: true, writable: true, value: sendBeacon });
    } else {
        delete navigator.sendBeacon;
    }

    const fetchSpy = vi.fn(() => Promise.resolve({ ok: true }));
    window.fetch = fetchSpy;

    // Aufloesbar von aussen: die Tests brauchen ein clearSessionKey, das
    // BEWUSST haengt, um zu belegen, dass vor ihm nicht navigiert wird.
    let resolveKeyDrop;
    const keyDropped = new Promise((resolve) => {
        resolveKeyDrop = resolve;
    });
    const clearSessionKey = vi.fn(() => (keyDropHangs ? keyDropped : Promise.resolve()));
    window.crypto_session = { clearSessionKey };

    const broadcast = vi.fn();
    window.syncOrchestrator = { broadcast };

    window.getCsrfToken = () => "TOKEN/42";

    const added = [];
    const restoreWindow = _install(window, added);
    const restoreDocument = _install(document, added);
    try {
        await import("../../src/static/js/quick-exit.js");
    } finally {
        restoreWindow();
        restoreDocument();
    }
    _wired = added;

    return { replace, reload, sendBeacon, fetchSpy, clearSessionKey, broadcast, resolveKeyDrop };
}

function online(extra = {}) {
    return {
        quickExitUrl: "https://example.org/wetter",
        privacyScreen: "0",
        quickExitLogoutUrl: LOGOUT_URL,
        ...extra,
    };
}

function overlay() {
    return document.getElementById("privacy-screen");
}

function press(key, modifiers = {}) {
    window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...modifiers }));
}

/** Mikrotask-Warteschlange leeren — die Navigation haengt seit R-L3-9 hinter
 *  dem abgewarteten ``clearSessionKey``, ist also nicht mehr synchron. */
async function tick() {
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

beforeEach(() => {
    window.localStorage.clear();
});

afterEach(() => {
    _unwire();
    vi.restoreAllMocks();
    vi.useRealTimers();
    delete window.syncOrchestrator;
});

describe("Konfiguration", () => {
    it("liest die Facility-Werte vom body und spiegelt sie in den localStorage", async () => {
        await freshLoad({ dataset: online({ privacyScreen: "1" }) });

        expect(window.quickExit.config()).toEqual({ url: "https://example.org/wetter", privacyScreen: true });
        expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY))).toEqual({
            url: "https://example.org/wetter",
            privacyScreen: true,
        });
    });

    it("liest offline den Spiegel, weil die Shell keine Konfiguration rendern darf", async () => {
        window.localStorage.setItem(
            STORAGE_KEY,
            JSON.stringify({ url: "https://example.org/wetter", privacyScreen: true })
        );

        await freshLoad({ dataset: null });

        expect(window.quickExit.config()).toEqual({ url: "https://example.org/wetter", privacyScreen: true });
    });

    it("faellt ohne Spiegel auf Vorgabeziel und abgeschalteten Sichtschutz zurueck", async () => {
        await freshLoad({ dataset: null });

        expect(window.quickExit.config()).toEqual({ url: DEFAULT_URL, privacyScreen: false });
    });

    it("verwirft ein Skript-Schema aus dem Spiegel", async () => {
        // Der Serverwert ist validiert, der Spiegel ist es nicht: er liegt im
        // same-origin beschreibbaren localStorage und laeuft direkt in
        // ``location.replace``.
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ url: "javascript:alert(1)", privacyScreen: false }));

        await freshLoad({ dataset: null });

        expect(window.quickExit.config().url).toBe(DEFAULT_URL);
    });
});

describe("Schnell verlassen", () => {
    it("verdeckt den Inhalt, meldet per Beacon ab und navigiert zur Zielseite", async () => {
        const { replace, sendBeacon } = await freshLoad({ dataset: online() });

        document.getElementById("quick-exit-button").click();

        // Verdecken passiert SYNCHRON, noch vor dem Warten auf die
        // Schluessel-Loeschung — das ist der Punkt der Reihenfolge.
        expect(overlay().hidden).toBe(false);
        // Beim Verlassen bleibt die Flaeche leer — "Zum Fortfahren ..." waere
        // dort die falsche Aussage.
        expect(document.getElementById("privacy-screen-panel").hidden).toBe(true);
        expect(document.getElementById("main-content").style.visibility).toBe("hidden");
        expect(sendBeacon).toHaveBeenCalledTimes(1);
        await tick();
        expect(replace).toHaveBeenCalledWith("https://example.org/wetter");
    });

    it("schickt den CSRF-Token als Formular-Body, nicht als Header", async () => {
        const { sendBeacon } = await freshLoad({ dataset: online() });

        window.quickExit.trigger();

        const [url, blob] = sendBeacon.mock.calls[0];
        expect(url).toBe(LOGOUT_URL);
        expect(blob.type).toBe("application/x-www-form-urlencoded");
        await expect(blob.text()).resolves.toBe("csrfmiddlewaretoken=TOKEN%2F42");
    });

    it("weicht ohne sendBeacon auf fetch mit keepalive aus", async () => {
        const { fetchSpy, replace } = await freshLoad({ dataset: online(), beacon: false });

        window.quickExit.trigger();

        expect(fetchSpy).toHaveBeenCalledTimes(1);
        const [url, init] = fetchSpy.mock.calls[0];
        expect(url).toBe(LOGOUT_URL);
        expect(init.method).toBe("POST");
        expect(init.keepalive).toBe(true);
        expect(init.headers["X-CSRFToken"]).toBe("TOKEN/42");
        expect(init.body).toBe("csrfmiddlewaretoken=TOKEN%2F42");
        await tick();
        expect(replace).toHaveBeenCalled();
    });

    it("weicht auch aus, wenn sendBeacon die Uebergabe ablehnt", async () => {
        const { fetchSpy, sendBeacon } = await freshLoad({ dataset: online(), beaconOk: false });

        window.quickExit.trigger();

        expect(sendBeacon).toHaveBeenCalledTimes(1);
        expect(fetchSpy).toHaveBeenCalledTimes(1);
    });

    it("navigiert auch dann, wenn der Logout-Versuch wirft", async () => {
        const { replace } = await freshLoad({ dataset: online(), beacon: false });
        window.fetch = () => {
            throw new Error("kein Netz");
        };

        await window.quickExit.trigger();

        expect(replace).toHaveBeenCalledWith("https://example.org/wetter");
    });

    it("verwirft offline den Sitzungsschluessel statt abzumelden", async () => {
        // Ohne Server gibt es keinen Logout — die einzige wirksame Sperre ist
        // der lokale Krypto-Schluessel (die Offline-Revocation greift danach).
        const { replace, sendBeacon, clearSessionKey } = await freshLoad({ dataset: null });

        await window.quickExit.trigger();

        expect(sendBeacon).not.toHaveBeenCalled();
        expect(clearSessionKey).toHaveBeenCalledTimes(1);
        expect(replace).toHaveBeenCalledWith(DEFAULT_URL);
    });
});

describe("Schluessel-Loeschung vor der Navigation (R-L3-9)", () => {
    it("navigiert erst, wenn die persistente Loeschung abgeschlossen ist", async () => {
        // Der Kern des HOCH-Befunds: clearSessionKey nullt den RAM-Key
        // synchron, loescht die IndexedDB-Zeile aber asynchron. Reisst
        // location.replace den Kontext vorher weg, bleibt der Schluessel
        // liegen — und die naechste Offline-Shell koennte ihn wieder laden.
        const { replace, resolveKeyDrop, clearSessionKey } = await freshLoad({
            dataset: null,
            keyDropHangs: true,
        });

        const laeuft = window.quickExit.trigger();

        expect(clearSessionKey).toHaveBeenCalledTimes(1);
        // Die Blende liegt bereits — der Bildschirm ist geschuetzt, waehrend
        // gewartet wird.
        expect(overlay().hidden).toBe(false);
        expect(document.getElementById("main-content").style.visibility).toBe("hidden");
        await tick();
        expect(replace).not.toHaveBeenCalled();

        resolveKeyDrop();
        await laeuft;

        expect(replace).toHaveBeenCalledWith(DEFAULT_URL);
    });

    it("navigiert nach dem Deckel trotzdem, wenn die Loeschung haengt", async () => {
        vi.useFakeTimers();
        const { replace } = await freshLoad({ dataset: null, keyDropHangs: true });

        const laeuft = window.quickExit.trigger();
        await tick();
        expect(replace).not.toHaveBeenCalled();

        await vi.advanceTimersByTimeAsync(window.quickExit.KEY_DROP_TIMEOUT_MS);
        await laeuft;

        expect(replace).toHaveBeenCalledWith(DEFAULT_URL);
    });

    it("navigiert ohne Krypto-Modul ohne Verzoegerung", async () => {
        // Testharness/Alt-Seite ohne crypto.js: kein Promise, kein Warten.
        const { replace } = await freshLoad({ dataset: null });
        delete window.crypto_session;

        await window.quickExit.trigger();

        expect(replace).toHaveBeenCalledWith(DEFAULT_URL);
    });

    it("sagt anderen Tabs Bescheid — erst NACH der Loeschung", async () => {
        // Der Empfaenger in crypto.js verankert den Schluessel wieder, wenn die
        // IndexedDB-Zeile noch steht. Ein Broadcast vor dem Loeschen waere also
        // schlimmer als keiner.
        const { broadcast, resolveKeyDrop } = await freshLoad({ dataset: null, keyDropHangs: true });

        const laeuft = window.quickExit.trigger();
        await tick();
        expect(broadcast).not.toHaveBeenCalled();

        resolveKeyDrop();
        await laeuft;

        expect(broadcast).toHaveBeenCalledWith({ type: "key-cleared" });
    });

    it("navigiert auch ohne Sync-Orchestrator", async () => {
        const { replace } = await freshLoad({ dataset: null });
        delete window.syncOrchestrator;

        await window.quickExit.trigger();

        expect(replace).toHaveBeenCalledWith(DEFAULT_URL);
    });
});

describe("Tastenkuerzel", () => {
    it("greift bei Strg+Umschalt+X auch aus einem fokussierten Eingabefeld", async () => {
        const { replace } = await freshLoad({ dataset: online() });
        document.getElementById("feld").focus();

        press("X", { ctrlKey: true, shiftKey: true });
        await tick();

        expect(replace).toHaveBeenCalledTimes(1);
    });

    it("greift bei Cmd+Umschalt+X (macOS)", async () => {
        const { replace } = await freshLoad({ dataset: online() });

        press("x", { metaKey: true, shiftKey: true });
        await tick();

        expect(replace).toHaveBeenCalledTimes(1);
    });

    it("greift bei Umschalt+Esc", async () => {
        const { replace } = await freshLoad({ dataset: online() });

        press("Escape", { shiftKey: true });
        await tick();

        expect(replace).toHaveBeenCalledTimes(1);
    });

    it("greift NICHT bei blossem Esc", async () => {
        // Ein Character-Key-Shortcut ohne Modifier waere ein WCAG-2.1.4-Fall
        // und wuerde das Schliessen der mobilen Suche kapern.
        const { replace } = await freshLoad({ dataset: online() });

        press("Escape");
        await tick();

        expect(replace).not.toHaveBeenCalled();
    });

    it("greift NICHT bei Strg+X (Ausschneiden)", async () => {
        const { replace } = await freshLoad({ dataset: online() });

        press("x", { ctrlKey: true });
        await tick();

        expect(replace).not.toHaveBeenCalled();
    });

    it("konsumiert die Kuerzel exklusiv — kein zweiter window-Listener feuert mit", async () => {
        // Die mobile Suche haengt mit @keydown.escape.window am selben window
        // (_navigation_mobile.html). ``preventDefault`` allein haette sie nicht
        // gestoppt: sie haette waehrend der Flucht ihr Overlay geschlossen und
        // den Fokus umgesetzt.
        await freshLoad({ dataset: online() });
        const konkurrent = vi.fn();
        window.addEventListener("keydown", konkurrent);
        try {
            press("Escape", { shiftKey: true });
            await tick();
            expect(konkurrent).not.toHaveBeenCalled();

            press("X", { ctrlKey: true, shiftKey: true });
            await tick();
            expect(konkurrent).not.toHaveBeenCalled();
        } finally {
            window.removeEventListener("keydown", konkurrent);
        }
    });

    it("laesst blosses Esc unangetastet — die mobile Suche schliesst weiter", async () => {
        await freshLoad({ dataset: online() });
        const konkurrent = vi.fn();
        window.addEventListener("keydown", konkurrent);
        try {
            press("Escape");
            await tick();
            expect(konkurrent).toHaveBeenCalledTimes(1);
        } finally {
            window.removeEventListener("keydown", konkurrent);
        }
    });
});

describe("Zurueck-Navigation aus dem bfcache", () => {
    it("verdeckt und laedt neu, wenn die Seite restauriert wird", async () => {
        const { reload } = await freshLoad({ dataset: online() });

        window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));

        expect(overlay().hidden).toBe(false);
        expect(reload).toHaveBeenCalledTimes(1);
    });

    it("laesst einen normalen Seitenaufbau in Ruhe", async () => {
        const { reload } = await freshLoad({ dataset: online() });

        window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: false }));

        expect(overlay().hidden).toBe(true);
        expect(reload).not.toHaveBeenCalled();
    });

    it("laedt in der Offline-Shell nicht neu — dort gibt es kein Netz", async () => {
        const { reload } = await freshLoad({ dataset: null });

        window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));

        expect(reload).not.toHaveBeenCalled();
    });
});

describe("Sichtschutz", () => {
    it("bleibt aus, solange die Einrichtung ihn nicht aktiviert hat", async () => {
        await freshLoad({ dataset: online({ privacyScreen: "0" }) });

        window.dispatchEvent(new Event("blur"));

        expect(overlay().hidden).toBe(true);
    });

    it("legt sich bei Fokusverlust ueber den Inhalt und nimmt ihn aus der Bedienung", async () => {
        await freshLoad({ dataset: online({ privacyScreen: "1" }) });

        window.dispatchEvent(new Event("blur"));

        expect(overlay().hidden).toBe(false);
        expect(document.getElementById("main-content").inert).toBe(true);
        expect(document.getElementById("nav-desktop").inert).toBe(true);
        expect(document.getElementById("main-content").style.visibility).toBe("hidden");
        expect(document.activeElement.id).toBe("privacy-screen-resume");
    });

    it("laesst kein Loch: auch der Skip-Link und die Knopfleiste sind stillgelegt", async () => {
        // Der Skip-Link haengt direkt am body, nicht in main oder nav — eine
        // Aufzaehlung "main + nav" haette ihn stehen lassen und die Blende
        // waere per Tabulator zu umgehen gewesen.
        await freshLoad({ dataset: online({ privacyScreen: "1" }) });

        window.dispatchEvent(new Event("blur"));

        expect(document.getElementById("skip-link").inert).toBe(true);
        expect(document.getElementById("skip-link").style.visibility).toBe("hidden");
        expect(document.getElementById("quick-exit-bar").inert).toBe(true);
        // Die Blende selbst bleibt bedienbar, sonst gaebe es keine Freigabe.
        expect(overlay().inert).toBe(false);
        expect(overlay().style.visibility).toBe("");
    });

    it("nutzt aria-hidden, wenn der Browser kein inert kennt", async () => {
        await freshLoad({ dataset: online({ privacyScreen: "1" }), inert: false });

        window.dispatchEvent(new Event("blur"));

        expect(document.getElementById("main-content").getAttribute("aria-hidden")).toBe("true");
        expect(document.getElementById("main-content").style.visibility).toBe("hidden");
    });

    it("greift auch beim Tab-Wechsel (visibilitychange)", async () => {
        await freshLoad({ dataset: online({ privacyScreen: "1" }) });
        Object.defineProperty(document, "hidden", { configurable: true, get: () => true });

        document.dispatchEvent(new Event("visibilitychange"));

        expect(overlay().hidden).toBe(false);
        Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
    });

    it("gibt den Fokus nach der Freigabe an das Ausgangselement zurueck", async () => {
        await freshLoad({ dataset: online({ privacyScreen: "1" }) });
        const ausgang = document.getElementById("im-inhalt");
        ausgang.focus();

        window.dispatchEvent(new Event("blur"));
        expect(document.activeElement.id).toBe("privacy-screen-resume");

        document.getElementById("privacy-screen-resume").click();

        expect(overlay().hidden).toBe(true);
        expect(document.getElementById("main-content").inert).toBe(false);
        expect(document.getElementById("main-content").style.visibility).toBe("");
        expect(document.activeElement.id).toBe("im-inhalt");
    });

    it("gibt auch per Enter und Leertaste frei", async () => {
        await freshLoad({ dataset: online({ privacyScreen: "1" }) });

        window.dispatchEvent(new Event("blur"));
        press("Enter");
        expect(overlay().hidden).toBe(true);

        window.dispatchEvent(new Event("blur"));
        press(" ");
        expect(overlay().hidden).toBe(true);
    });

    it("loest sich NICHT von selbst — kein Timer, keine Bewegung (WCAG 2.2.1)", async () => {
        vi.useFakeTimers();
        await freshLoad({ dataset: online({ privacyScreen: "1" }) });

        window.dispatchEvent(new Event("blur"));
        vi.advanceTimersByTime(10 * 60 * 1000);

        expect(overlay().hidden).toBe(false);
    });

    it("armiert nicht doppelt und ueberschreibt das gemerkte Element nicht", async () => {
        await freshLoad({ dataset: online({ privacyScreen: "1" }) });
        document.getElementById("im-inhalt").focus();

        window.dispatchEvent(new Event("blur"));
        window.dispatchEvent(new Event("blur"));
        document.getElementById("privacy-screen-resume").click();

        expect(document.activeElement.id).toBe("im-inhalt");
    });
});
