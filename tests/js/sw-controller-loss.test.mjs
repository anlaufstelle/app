// Refs #1951: Ein VERLORENER Service-Worker-Controller ist kein Update — und
// darf die Seite nicht neu laden.
//
// Der Logout antwortet mit ``Clear-Site-Data: "storage", "cache"``
// (core/views/auth.py, ``CustomLogoutView.dispatch``). Chromium raeumt damit
// auch die Service-Worker-Registrierung ab, und zwar WAEHREND die
// Logout-Weiterleitung nach ``/login/`` noch fliegt. Die noch lebende alte
// Seite verliert dadurch ihren Controller und bekommt ``controllerchange``.
// Wurde das als SW-*Update* gedeutet, rief ``_reloadIfSafe()`` ->
// ``window.location.reload()`` — und dieser Reload brach die laufende
// Logout-Navigation ab (``net::ERR_ABORTED``). Der Nutzer landete dann nicht
// auf ``/login/``, sondern auf einem ``/login/?next=<alte URL>`` — inklusive
// HTMX-Partial-URLs als ``next``-Ziel.
//
// sw-register.js ist eine IIFE und liest ``navigator.serviceWorker.controller``
// EINMAL beim Laden (``wasControlled``). Jeder Fall braucht darum einen
// frischen Modul-Import (``vi.resetModules()``) mit vorab gesetzten Stubs.
import { describe, it, expect, afterEach, vi } from "vitest";

/** Service-Worker-Stub mit ausloesbaren Events (jsdom hat kein serviceWorker). */
function installServiceWorkerStub(controlled) {
    const listeners = {};
    const registration = {
        waiting: null,
        addEventListener: (type, cb) => {
            (listeners["reg:" + type] = listeners["reg:" + type] || []).push(cb);
        },
    };
    const sw = {
        controller: controlled ? { scriptURL: "http://localhost/sw.js" } : null,
        addEventListener: (type, cb) => {
            (listeners[type] = listeners[type] || []).push(cb);
        },
        register: () => Promise.resolve(registration),
    };
    Object.defineProperty(window.navigator, "serviceWorker", {
        configurable: true,
        value: sw,
    });
    return {
        sw,
        dispatch(type) {
            (listeners[type] || []).forEach((cb) => cb({ type }));
        },
    };
}

// Jede Modul-Instanz haengt sich ``input``/``change``/``submit``-Listener an
// ``document``. Nach ``vi.resetModules()`` bleiben die ALTEN Listener aktiv und
// wuerden mitfeuern — der Re-Entrancy-Guard (``form._logoutWipeInFlight``) der
// alten Instanz wuerde die neue am eigenen Logout-Pfad vorbeilaufen lassen. Wir
// merken uns darum, was ein Import verdrahtet, und raeumen es wieder ab.
let _docListeners = [];

function removeDocListeners() {
    _docListeners.forEach(([type, cb, opts]) => document.removeEventListener(type, cb, opts));
    _docListeners = [];
}

/** sw-register.js frisch laden, mit Reload-Spy und SW-Stub davor. */
async function freshLoad({ controlled }) {
    removeDocListeners();
    vi.resetModules();
    document.body.innerHTML = "";
    const reload = vi.fn();
    Object.defineProperty(window, "location", {
        configurable: true,
        writable: true,
        value: { reload, href: "http://localhost/", pathname: "/" },
    });
    const harness = installServiceWorkerStub(controlled);

    const added = [];
    const origAdd = document.addEventListener.bind(document);
    document.addEventListener = (type, cb, opts) => {
        added.push([type, cb, opts]);
        origAdd(type, cb, opts);
    };
    try {
        await import("../../src/static/js/sw-register.js");
    } finally {
        delete document.addEventListener;
        _docListeners = added;
    }
    // register().then(...) abarbeiten lassen
    await new Promise((resolve) => setTimeout(resolve, 0));
    return { ...harness, reload };
}

/** Auf die Microtask-Kette in _reloadIfSafe()/_queuePending() warten. */
function settle() {
    return new Promise((resolve) => setTimeout(resolve, 10));
}

function makeLogoutForm() {
    const form = document.createElement("form");
    form.setAttribute("action", "/logout/");
    form.submit = vi.fn();
    document.body.appendChild(form);
    return form;
}

afterEach(() => {
    removeDocListeners();
    delete window.crypto_session;
    delete window.offlineStore;
    delete window.offlineQueue;
    // Die Logout-Marke wird im Produktivcode absichtlich nie zurueckgesetzt
    // (die Navigation ersetzt die Seite) — im Test muss sie weg, sonst
    // faerbt sie in Folgetests ab.
    delete window.__logoutInFlight;
    document.body.innerHTML = "";
    vi.restoreAllMocks();
});

describe("controllerchange: Controller-Verlust ist kein Update", () => {
    it("laedt NICHT neu, wenn der Controller wegfaellt (Clear-Site-Data beim Logout)", async () => {
        const h = await freshLoad({ controlled: true });

        // Clear-Site-Data: "storage" hat die SW-Registrierung abgeraeumt.
        h.sw.controller = null;
        h.dispatch("controllerchange");
        await settle();

        expect(h.reload).not.toHaveBeenCalled();
    });

    it("laedt weiterhin neu, wenn ein NEUER Controller uebernimmt (echtes Update)", async () => {
        const h = await freshLoad({ controlled: true });

        h.sw.controller = { scriptURL: "http://localhost/sw.js?v=2" };
        h.dispatch("controllerchange");
        await settle();

        expect(h.reload).toHaveBeenCalledTimes(1);
    });

    it("laedt beim ERSTEN Controller nicht neu (bestehender wasControlled-Guard)", async () => {
        const h = await freshLoad({ controlled: false });

        h.sw.controller = { scriptURL: "http://localhost/sw.js" };
        h.dispatch("controllerchange");
        await settle();

        expect(h.reload).not.toHaveBeenCalled();
    });

    it("laedt nach einem Controller-Verlust auch bei erneuter Uebernahme nicht neu", async () => {
        const h = await freshLoad({ controlled: true });

        // Verlust ...
        h.sw.controller = null;
        h.dispatch("controllerchange");
        await settle();
        // ... und die /login/-Seite registriert einen neuen Worker, der claimt.
        h.sw.controller = { scriptURL: "http://localhost/sw.js" };
        h.dispatch("controllerchange");
        await settle();

        expect(h.reload).not.toHaveBeenCalled();
    });
});

describe("controllerchange: kein Auto-Reload waehrend eines Logouts", () => {
    it("unterdrueckt den Reload, wenn die Logout-Navigation schon laeuft", async () => {
        const h = await freshLoad({ controlled: true });

        // Logout abgeschickt: Wipe durch, programmatischer POST unterwegs.
        const form = makeLogoutForm();
        form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        await vi.waitFor(() => expect(form.submit).toHaveBeenCalledTimes(1));

        // Jetzt aktiviert sich ein echtes SW-Update mitten in der Navigation.
        h.sw.controller = { scriptURL: "http://localhost/sw.js?v=2" };
        h.dispatch("controllerchange");
        await settle();

        expect(h.reload).not.toHaveBeenCalled();
    });

    // Refs #1951: Der Logout ist ab dem KLICK geschuetzt, nicht erst ab
    // ``form.submit()``. Ein Reload im Wipe-Fenster wuerde den Logout nicht
    // verzoegern, sondern verwerfen — der POST kaeme nie, der Nutzer bliebe
    // angemeldet, waehrend der Wipe die Offline-Daten schon geloescht hat.
    it("schuetzt schon das Wipe-Fenster, bevor die Navigation startet", async () => {
        const h = await freshLoad({ controlled: true });

        // Wipe, der (noch) nicht fertig wird: ``form.submit()`` ist also noch
        // nicht gelaufen.
        window.crypto_session = { wipeOfflineState: vi.fn(() => new Promise(() => {})) };
        const form = makeLogoutForm();
        form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        await settle();
        expect(form.submit).not.toHaveBeenCalled();

        h.sw.controller = { scriptURL: "http://localhost/sw.js?v=2" };
        h.dispatch("controllerchange");
        await settle();

        expect(h.reload).not.toHaveBeenCalled();
    });

    // Refs #1951: Selbstheilung. Kommt es nie zur Navigation (z.B. die
    // Nav-Form verschwindet im Wipe-Fenster aus dem DOM und ``form.submit()``
    // laeuft ins Leere), darf das Dokument nicht dauerhaft reload-blind
    // bleiben.
    it("gibt den Reload nach Ablauf des Deckels wieder frei", async () => {
        const h = await freshLoad({ controlled: true });

        // Marke aus einem Logout, der nie navigiert hat — aelter als 10 s.
        window.__logoutInFlight = Date.now() - 11_000;

        h.sw.controller = { scriptURL: "http://localhost/sw.js?v=2" };
        h.dispatch("controllerchange");
        await settle();

        expect(h.reload).toHaveBeenCalledTimes(1);
    });

    it("ignoriert eine Marke, die kein Zeitstempel ist", async () => {
        const h = await freshLoad({ controlled: true });

        // Alter Boolean-Vertrag darf nicht versehentlich alles blockieren.
        window.__logoutInFlight = true;

        h.sw.controller = { scriptURL: "http://localhost/sw.js?v=2" };
        h.dispatch("controllerchange");
        await settle();

        expect(h.reload).toHaveBeenCalledTimes(1);
    });
});

// Refs #2360: die bisherigen Faelle oben setzen die Marke VOR dem
// controllerchange, also bevor ``_reloadIfSafe()`` ueberhaupt startet.
// ``_reloadIfSafe()`` liest die Marke aber nur EINMAL am Einstieg und wartet
// danach auf den asynchronen ``_queuePending()`` (IndexedDB-Zaehlung der
// Offline-Warteschlange) — startet ein Schnell-Verlassen ERST waehrend
// dieses Wartens, sah der anschliessende ``_doReload()`` die Marke bisher
// nie.
describe("controllerchange: Logout-Marke kann waehrend _queuePending() gesetzt werden (Refs #2360)", () => {
    it("unterdrueckt den Reload, wenn die Marke erst waehrend des Warteschlangen-Checks gesetzt wird", async () => {
        const h = await freshLoad({ controlled: true });
        window.offlineQueue = {
            getQueueCount: () =>
                Promise.resolve().then(() => {
                    // Das Schnell-Verlassen setzt die Marke MITTEN im
                    // asynchronen Zwischenschritt — vor der ersten
                    // Marken-Pruefung in ``_reloadIfSafe()`` war sie noch nicht da.
                    window.__logoutInFlight = Date.now();
                    return 0;
                }),
        };

        h.sw.controller = { scriptURL: "http://localhost/sw.js?v=2" };
        h.dispatch("controllerchange");
        await settle();

        expect(h.reload).not.toHaveBeenCalled();
    });

    it("Kontrollfall: laedt weiterhin neu, wenn waehrenddessen KEINE Marke gesetzt wird", async () => {
        const h = await freshLoad({ controlled: true });
        window.offlineQueue = {
            getQueueCount: () => Promise.resolve().then(() => 0),
        };

        h.sw.controller = { scriptURL: "http://localhost/sw.js?v=2" };
        h.dispatch("controllerchange");
        await settle();

        expect(h.reload).toHaveBeenCalledTimes(1);
    });
});
