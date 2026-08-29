// Refs #2060: ``saveOfflineEdit`` hatte GAR KEINE Fehlerbehandlung — ein
// nacktes ``db.events.put`` ohne try/catch, waehrend die Schwesterfunktion
// ``saveClientBundle`` (offline-bundles.js:150-158) Quota-Fehler laengst auf
// einen stabilen Vertrag normalisiert (``name === "QuotaExceededError"``).
//
// Die Folge: Dieselbe Ursache — volle Platte — kam bei der Offline-Mitnahme
// als klare Meldung an und beim Speichern einer Offline-Bearbeitung als roher
// Dexie-Fehler, den die Aufrufer (offline-edit.js:252,298,458,510) ungeprueft
// weiterreichten. Der Vertrag wird hier gespiegelt.
//
// Echter Verbund (echtes Dexie, echte crypto.js) — nur der eine Schreibpfad
// wird gestellt; Muster autosave-draft-person.test.mjs.

import { importOfflineStoreModules } from "./_offline_store_modules.mjs";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import Dexie from "dexie";

let store;

const PK = "33333333-3333-4333-8333-333333333333";

beforeAll(async () => {
    window.Dexie = Dexie;
    globalThis.Dexie = Dexie;
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.deriveSessionKey("correct horse battery", "c2FsdHNhbHRzYWx0");
    await importOfflineStoreModules();
    store = window.offlineStore;
});

afterEach(() => {
    vi.restoreAllMocks();
});

function edit() {
    return { pk: PK, clientPk: "", localStatus: "modified", data: { note: "hello" } };
}

function quotaError() {
    const error = new Error("QuotaExceededError");
    error.name = "QuotaExceededError";
    return error;
}

/** Dexie kapselt den Quota-Fehler beim Transaktionsabbruch in ``e.inner``. */
function wrappedQuotaError() {
    const error = new Error("AbortError");
    error.name = "AbortError";
    error.inner = quotaError();
    return error;
}

describe("saveOfflineEdit error contract", () => {
    it("still writes the edit on the happy path", async () => {
        await store.saveOfflineEdit(edit());

        const stored = await store.getOfflineEvent(PK);
        expect(stored.data.note).toBe("hello");
    });

    it("keeps rejecting a malformed record", async () => {
        await expect(store.saveOfflineEdit({})).rejects.toThrow("MalformedEdit");
    });

    it("normalises a plain quota error like saveClientBundle does", async () => {
        vi.spyOn(store.db.events, "put").mockRejectedValue(quotaError());

        await expect(store.saveOfflineEdit(edit())).rejects.toMatchObject({
            name: "QuotaExceededError",
            message: "OfflineQuotaExceeded",
        });
    });

    it("normalises a quota error wrapped by Dexie", async () => {
        vi.spyOn(store.db.events, "put").mockRejectedValue(wrappedQuotaError());

        await expect(store.saveOfflineEdit(edit())).rejects.toMatchObject({
            name: "QuotaExceededError",
            message: "OfflineQuotaExceeded",
        });
    });

    it("passes every other failure through untouched", async () => {
        const other = new Error("DataError");
        other.name = "DataError";
        vi.spyOn(store.db.events, "put").mockRejectedValue(other);

        await expect(store.saveOfflineEdit(edit())).rejects.toBe(other);
    });
});
