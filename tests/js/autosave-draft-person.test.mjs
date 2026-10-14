// Refs #1774: Der Autosave-Entwurf von /events/new/ war fuer den
// personen-genauen Offline-Widerruf (#1760) unsichtbar.
//
// Der ``formKey`` bildet nur ``user`` + ``pathname`` ("autosave_<uid>_/events/
// new/") — keine Personen-UUID. Und der Payload trug sie ebenfalls nicht:
// ``getFormData`` ueberspringt versteckte Eingabefelder, und genau eines ist
// das ``client``-Feld der Personenauswahl (Alpine-gebunden an ``selectedId``).
// Der Entwurf enthielt damit Klartext-Notizen ZU einer Person, ohne dass der
// Widerruf ihn je einer Person zuordnen konnte.
//
// Fix: Ein Formularfeld kann sich per ``data-autosave-person`` als Traeger des
// Personenbezugs ausweisen; sein Wert wandert unter dem reservierten Schluessel
// ``__personPk`` IN den verschluesselten Entwurfs-Umschlag (nie als
// Plaintext-Row-Feld — das waere unverschluesselte PII-Verknuepfungsmetadate,
// Refs #1476/R9). Damit greift der bestehende Payload-Scan des Purges.
//
// Getestet gegen den ECHTEN offline-store.js (echtes Dexie, echte crypto.js);
// nur ``fetch`` ist gemockt — dieselbe Schicht wie offline-revocations.test.mjs.
// Refs #1838: Modul-Liste des offline-store-Verbunds lebt an EINER Stelle.
import { importOfflineStoreModules } from "./_offline_store_modules.mjs";
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from "vitest";
import Dexie from "dexie";

let store;
let autosave;

const PK_A = "11111111-1111-4111-8111-111111111111";
const PK_B = "22222222-2222-4222-8222-222222222222";

beforeAll(async () => {
    window.Dexie = Dexie;
    globalThis.Dexie = Dexie;
    // Refs #1828: Endpunkt-Literale leben in url-patterns.js (ENDPOINTS) —
    // wie in base.html VOR offline-store.js laden.
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.deriveSessionKey("correct horse battery", "c2FsdHNhbHRzYWx0");
    await importOfflineStoreModules();
    store = window.offlineStore;
    await import("../../src/static/js/autosave.js");
    autosave = window.autosave;
});

beforeEach(async () => {
    await store.purgeAll();
    document.body.innerHTML = "";
});

afterEach(() => {
    vi.unstubAllGlobals();
});

/*
 * Baut das reale Formular-Geruest von /events/new/ nach: ein sichtbares
 * Notizfeld, das versteckte, Alpine-gebundene ``client``-Feld und (optional)
 * ein weiteres verstecktes Feld ohne Opt-in.
 */
function eventCreateForm(selectedClientId) {
    document.body.dataset.userId = "u1";
    const form = document.createElement("form");
    form.setAttribute("data-autosave", "");
    form.innerHTML =
        '<input type="hidden" name="csrfmiddlewaretoken" value="tok">' +
        '<input type="hidden" name="expected_updated_at" value="2026-08-04T10:00:00+00:00">' +
        '<input type="hidden" name="client" data-autosave-person value="' +
        (selectedClientId || "") +
        '">' +
        '<textarea name="notiz">Klartext-PII</textarea>';
    document.body.appendChild(form);
    return form;
}

function mockRevocationFetch(pks) {
    const response = {
        ok: true,
        status: 200,
        headers: { get: () => null },
        json: async () => ({
            revocations: pks,
            server_time: new Date().toISOString(),
            retention_seconds: 345600,
        }),
    };
    vi.stubGlobal(
        "fetch",
        vi.fn(async () => response)
    );
}

describe("#1774 — Entwurfs-Payload traegt den Personenbezug", () => {
    it("schreibt die gewaehlte Person unter dem reservierten Schluessel mit", () => {
        const form = eventCreateForm(PK_A);

        const data = autosave._buildDraftPayload(form, "");

        expect(data[autosave._PERSON_KEY]).toBe(PK_A);
        expect(data.notiz).toBe("Klartext-PII");
    });

    it("laesst den Schluessel weg, solange keine Person gewaehlt ist", () => {
        const form = eventCreateForm("");

        const data = autosave._buildDraftPayload(form, "");

        expect(autosave._PERSON_KEY in data).toBe(false);
    });

    it("schreibt versteckte Felder OHNE Opt-in weiterhin nicht mit", () => {
        const form = eventCreateForm(PK_A);

        const data = autosave._buildDraftPayload(form, "");

        expect("expected_updated_at" in data).toBe(false);
        expect("csrfmiddlewaretoken" in data).toBe(false);
        // Das Personenfeld landet NUR unter dem reservierten Schluessel, nicht
        // zusaetzlich unter seinem Feldnamen (``restoreFormData`` darf es nicht
        // als Formularwert zurueckschreiben).
        expect("client" in data).toBe(false);
    });

    it("haelt den Personenbezug nach einem Wiederherstellen fest (Feld noch leer)", () => {
        // Nach dem Reload ist die Alpine-Auswahl zurueckgesetzt (das versteckte
        // Feld ist leer), der Entwurfsinhalt betrifft aber weiter dieselbe
        // Person. Der Bezug darf nicht still verloren gehen — sonst waere der
        // wiederhergestellte Entwurf fuer den Widerruf wieder unsichtbar.
        const form = eventCreateForm("");

        const data = autosave._buildDraftPayload(form, PK_A);

        expect(data[autosave._PERSON_KEY]).toBe(PK_A);
    });

    it("keeps the restored person when a different person is selected", () => {
        const form = eventCreateForm(PK_B);

        const data = autosave._buildDraftPayload(form, PK_A);

        expect(data[autosave._PERSON_KEY]).toBe(PK_A);
    });
});

describe("#1774 — Widerruf erfasst den /events/new/-Entwurf", () => {
    it("verwirft den Entwurf der widerrufenen Person", async () => {
        const form = eventCreateForm(PK_A);
        await store.putEncrypted("drafts", {
            formKey: "autosave_u1_/events/new/",
            updatedAt: Date.now(),
            data: autosave._buildDraftPayload(form, ""),
        });
        mockRevocationFetch([PK_A]);

        const result = await store.applyClientRevocations();

        expect(result.purgedDrafts).toBe(1);
        expect(await store.db.drafts.count()).toBe(0);
    });

    it("laesst den Entwurf einer anderen Person stehen", async () => {
        const form = eventCreateForm(PK_B);
        await store.putEncrypted("drafts", {
            formKey: "autosave_u1_/events/new/",
            updatedAt: Date.now(),
            data: autosave._buildDraftPayload(form, ""),
        });
        mockRevocationFetch([PK_A]);

        await store.applyClientRevocations();

        expect(await store.db.drafts.count()).toBe(1);
    });

    it("laesst einen Entwurf OHNE Personenbezug stehen", async () => {
        const form = eventCreateForm("");
        await store.putEncrypted("drafts", {
            formKey: "autosave_u1_/events/new/",
            updatedAt: Date.now(),
            data: autosave._buildDraftPayload(form, ""),
        });
        mockRevocationFetch([PK_A]);

        await store.applyClientRevocations();

        expect(await store.db.drafts.count()).toBe(1);
    });

    it("laesst einen Alt-Entwurf ohne den Schluessel stehen (tolerante Lesung, Rest-Grenze)", async () => {
        // Vor #1774 geschriebene Entwuerfe tragen den Personenbezug nicht — sie
        // werden tolerant gelesen (kein Crash, keine Migration) und fallen bis
        // zum naechsten Autosave-Tick bzw. zur 48h-Draft-TTL aus der Reichweite.
        await store.putEncrypted("drafts", {
            formKey: "autosave_u1_/events/new/",
            updatedAt: Date.now(),
            data: { notiz: "Alt-Entwurf ohne Personenbezug" },
        });
        mockRevocationFetch([PK_A]);

        await store.applyClientRevocations();

        expect(await store.db.drafts.count()).toBe(1);
    });
});
