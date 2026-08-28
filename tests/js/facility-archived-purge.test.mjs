// Refs #2067 (Cross-Review Finding 1): Archivierung der Einrichtung muss den
// OFFLINE-Bestand erreichen.
//
// Ausgangslage: `revalidateCachedClient`/`revalidateCachedFacility` purgen nur
// bei 401/404/410. Ein nacktes 403 ist seit Refs #1354 BEWUSST kein
// Purge-Grund — echter Rechteentzug erreicht den Client als Login-Redirect,
// ein 403 ist sonst CSRF-/Rate-Limit-/Proxy-Rauschen und darf den
// Aussendienst-Cache nicht leeren. Die Sperr-403 der
// `FacilityScopeMiddleware` trug damit keine Wirkung: Bundles und der
// abgeleitete Sitzungsschluessel blieben liegen, die Nutzerin konnte wieder
// offline gehen und weiterlesen.
//
// Der Fix unterscheidet beide Faelle ueber den Header `X-Facility-Archived`.
// Diese Suite pinnt BEIDE Richtungen — sonst waere der Fix eine Regression
// gegen #1354.
//
// Echter Stack (Dexie + echte WebCrypto + echte crypto.js) wie
// offline-store-aad.test.mjs: der Nachweis „der Schluessel faellt mit" laesst
// sich an einem gemockten Store nicht fuehren.
import Dexie from "dexie";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { importOfflineStoreModules } from "./_offline_store_modules.mjs";

let store;

const PASSPHRASE = "correct horse battery";
const SALT = "c2FsdHNhbHRzYWx0";

beforeAll(async () => {
    window.Dexie = Dexie;
    globalThis.Dexie = Dexie;
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.deriveSessionKey(PASSPHRASE, SALT);
    await importOfflineStoreModules();
    store = window.offlineStore;
});

beforeEach(async () => {
    // Der Archiv-Wipe raeumt den Sitzungsschluessel mit ab — jeder Test
    // startet deshalb mit frisch abgeleitetem Schluessel und leerem Store.
    await window.crypto_session.deriveSessionKey(PASSPHRASE, SALT);
    await store.purgeAll();
    delete global.fetch;
});

function bundle(pk, pseudonym) {
    return {
        client: { pk: pk, pseudonym: pseudonym, is_active: true, last_contact: null },
        cases: [],
        events: [],
        workitems: [],
        document_types: [],
        assignable_users: [],
        generated_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 3600_000).toISOString(),
        ttl: 3600,
        schema_version: 2,
    };
}

/** 403 der Sperrseite — mit oder ohne den maschinenlesbaren Marker. */
function forbidden({ archived }) {
    const headers = archived ? { "X-Facility-Archived": "1" } : {};
    return new Response("gesperrt", { status: 403, headers: headers });
}

describe("Archivierte Einrichtung — Offline-Bestand faellt", () => {
    it("purgt Bundles UND Sitzungsschluessel bei der markierten 403", async () => {
        await store.saveClientBundle(bundle("A", "Falke"));
        expect(await store.countOfflineClients()).toBe(1);
        expect(window.crypto_session.hasSessionKey()).toBe(true);
        global.fetch = vi.fn(async () => forbidden({ archived: true }));

        const result = await store.revalidateCachedClient("A");

        expect(result).toBe("archived");
        expect(await store.countOfflineClients()).toBe(0);
        // Der Kern des Befunds: ohne den Schluessel ist auch ein spaeter
        // hereingeholtes Chiffrat unlesbar.
        expect(window.crypto_session.hasSessionKey()).toBe(false);
    });

    it("laesst den Cache bei einer 403 OHNE Marker unangetastet (Refs #1354)", async () => {
        await store.saveClientBundle(bundle("A", "Falke"));
        global.fetch = vi.fn(async () => forbidden({ archived: false }));

        const result = await store.revalidateCachedClient("A");

        expect(result).toBe("error");
        expect(await store.countOfflineClients()).toBe(1);
        expect(window.crypto_session.hasSessionKey()).toBe(true);
    });

    it("purgt auch ueber die Facility-Meta-Revalidierung", async () => {
        await store.saveClientBundle(bundle("A", "Falke"));
        global.fetch = vi.fn(async () => forbidden({ archived: true }));

        const result = await store.revalidateCachedFacility();

        expect(result).toBe("archived");
        expect(await store.countOfflineClients()).toBe(0);
        expect(window.crypto_session.hasSessionKey()).toBe(false);
    });

    it("bricht die Batch-Schleife ab und meldet den Archiv-Fall", async () => {
        await store.saveClientBundle(bundle("A", "Falke"));
        await store.saveClientBundle(bundle("B", "Reiher"));
        global.fetch = vi.fn(async () => forbidden({ archived: true }));

        const summary = await store.revalidateCachedClients();

        expect(summary.archived).toBe(true);
        // Nach dem ersten Treffer ist alles weg — ein zweiter Durchlauf liefe
        // gegen geloeschte Zeilen und holte im schlimmsten Fall neue.
        expect(global.fetch).toHaveBeenCalledTimes(1);
        expect(await store.countOfflineClients()).toBe(0);
    });

    it("purgt auch mit ungesyncten Edits — Sicherheit vor Datenerhalt", async () => {
        await store.saveClientBundle(bundle("A", "Falke"));
        await store.db.events.put({ pk: "E1", clientPk: "A", localStatus: "modified", data: {} });
        global.fetch = vi.fn(async () => forbidden({ archived: true }));

        const result = await store.revalidateCachedClient("A");

        expect(result).toBe("archived");
        expect(await store.db.events.count()).toBe(0);
    });
});
